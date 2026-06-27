#!/usr/bin/env python3
"""
KAVACH allocation — core data model, IM3 engine, and shared CP-SAT helpers.

This module is the dependency root: it imports nothing from the rest of the
package, so `colour`/`slots`/`pipeline`/`report` (in allocation_solver.py),
`stagger.py` and `dense_area.py` can all import from it without a cycle. Keeping
`Pair`/`Problem`/the IM3 engine here is what removed the previous circular import
between allocation_solver and stagger (audit CQ-2/CQ-3).
"""
from __future__ import annotations
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict

# Set KAVACH_NO_CPSAT=1 to force the pure-Python fallback (skip OR-Tools CP-SAT).
# Useful when a local OR-Tools install is broken/crashing: the tool still produces
# a valid spectrum-minimal, TAN/SPN-compliant plan, but without the CP-SAT-only
# time-domain staggering (residual three-tone IM is reported for the ISA).
try:
    if os.environ.get("KAVACH_NO_CPSAT"):
        raise ImportError("CP-SAT disabled via KAVACH_NO_CPSAT")
    from ortools.sat.python import cp_model
    _HAS_CPSAT = True
except Exception:                                    # pragma: no cover
    cp_model = None
    _HAS_CPSAT = False

GRID_MHZ = 0.025          # 25 kHz channel grid
SOLVER_TIME_S = 30        # CP-SAT colour-phase time budget (seconds)
MARKER_MS = 27.5          # TDMA marker spacing (SPN/196 Annexure-C)
KAVACH_F0 = 402.350       # control/emergency centre frequency (MHz)

# Fixed CP-SAT search parameters so the generated allocation is REPRODUCIBLE
# across runs and machines (audit B7). A fixed seed is not sufficient on its
# own: under a WALL-CLOCK time limit, multi-worker search races to report
# whichever optimal solution a worker holds when the timer fires, so the same
# input can yield different (equally-optimal) schedules. A SINGLE worker makes
# the search deterministic, which for a single-section solve (seconds) is the
# right trade -- an auditable planning record must be reproducible, and the lost
# parallelism is immaterial at this scale.
SOLVER_SEED = 1
SOLVER_WORKERS = 1

# Input-size backstop (audit B3): this tool solves ONE railway section per run.
# Reject pathological inputs with a clear "decompose" error instead of letting
# the CP-SAT model build / IM-tuple enumeration exhaust memory before the time
# cap can convert it into a clean failure.
MAX_STATIONS = 1000
MAX_PALETTE = 128

# IM3 order selector shared by every caller (single source of truth, audit DUP-2)
ORDERS = {"full": ("two", "three"), "two_tone": ("two",), "none": ()}


# ===========================================================================
# RESULT / SCHEDULE TYPES  (audit DM-1)
# Explicit, checkable shapes for what were previously untyped ~11-key dicts.
# These are TypedDicts: at runtime they are ordinary dicts, so every existing
# `result['colour']` / `sched[s]['loco']` access keeps working unchanged -- the
# annotations only make the contract visible to readers and type checkers.
# ===========================================================================
class SlotEntry(TypedDict):
    """One station's slot assignment: a contiguous Stationary Tx window and the
    (non-adjacent) Loco slots, as TDMA marker numbers."""
    station: List[int]
    loco: List[int]


# A schedule maps each station id (int or str) to its SlotEntry.
Schedule = Dict[Any, SlotEntry]


class SolveResult(TypedDict):
    """What solve() returns (the colouring + schedule + invariants)."""
    colour: Dict[Any, int]            # station id -> pair id
    used_pairs: Set[int]              # distinct pair ids in use
    schedule: Schedule
    spectrum: int                     # len(used_pairs)
    errors: List[str]                 # validate() output ([] == compliant)
    im3_level: str                    # "full" | "two_tone" | "none"
    slot_strategy: str                # "offset0" | "compact"
    gap_slots: int                    # frame-offset-0 gap (markers)


class CompliantResult(SolveResult):
    """What solve_compliant() returns: a SolveResult plus the staggering report."""
    staggered: bool                   # did time-domain staggering actually run?
    im_coincidence: Optional[Tuple[int, Dict[Tuple[int, ...], int]]]
    im3_note: str


def make_cpsat_solver(time_s):
    """A `CpSolver` with a fixed time budget, seed and worker count so results
    are reproducible across runs and machines (audit B7)."""
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_s
    solver.parameters.random_seed = SOLVER_SEED
    solver.parameters.num_workers = SOLVER_WORKERS
    return solver


def cpsat_solve(solver, model, what):
    """Solve a CP-SAT model and raise a STATUS-SPECIFIC error on failure.

    A time-limit (UNKNOWN) raises `TimeoutError`; genuine infeasibility / an
    invalid model raises `ValueError`. The distinct types matter (audit B1): a
    time-out is a *recoverable* condition for an enhancement phase (staggering
    degrades to plain slots) but a *fatal* one for a mandatory phase (colour
    assignment must surface it, not silently treat it as IM3 infeasibility)."""
    st = solver.Solve(model)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return st
    name = solver.StatusName(st)
    if st == cp_model.UNKNOWN:
        raise TimeoutError(
            f"{what}: no solution within "
            f"{solver.parameters.max_time_in_seconds:.0f}s ({name}); "
            f"increase the time budget or relax the constraints")
    if st == cp_model.MODEL_INVALID:
        raise ValueError(f"{what}: model invalid ({name}) - internal error")
    raise ValueError(f"{what}: infeasible ({name})")


def default_gap_slots(ms=200.0):
    """Markers needed to clear `ms` (SPN/196 17.14 needs 150-200 ms before the
    station slot for frame-offset 0)."""
    return math.ceil(ms / MARKER_MS)


# ===========================================================================
# DATA MODEL
# ===========================================================================
@dataclass
class Pair:
    """One atomic colour = a duplex frequency pair."""
    id: int
    fS: float             # downlink (Station Tx), MHz
    fM: float             # uplink   (Onboard Tx), MHz


@dataclass
class Problem:
    stations: list                       # station ids, IN GEOGRAPHIC ORDER
    sta_slots: dict                      # id -> #Stationary KAVACH Tx slots
    loco_slots: dict                     # id -> #Loco KAVACH Tx slots
    palette: list                        # list[Pair]  (atomic colours)
    reuse_window: int = 4                # stations within this many positions interfere
    edges: list | None = None            # explicit interference edges (overrides below)
    positions: dict | None = None        # id -> position (km); used with rf_range_km
    rf_range_km: float | None = None     # RF interference radius (real RF-range model)
    allowed: dict = field(default_factory=dict)   # id -> set(pair ids); default = all
    f0: float | None = None
    include_f0_in_im3: bool = False      # f0 time-separated (P47-P70) -> default off
    num_slots: int = 45                  # working markers P2..P45 (+P1/P46 reserved)
    reserved_slots: set = field(default_factory=lambda: {1, 46})
    loco_nonadjacent: bool = True

    def __post_init__(self):
        self._check_size()
        if self.edges is None:
            if self.positions is not None and self.rf_range_km is not None:
                self.edges = self._distance_edges()
            else:
                self.edges = self._window_edges()

    def _check_size(self):
        """Reject inputs too large for a single-section solve (audit B3)."""
        n = len(self.stations)
        if n > MAX_STATIONS:
            raise ValueError(
                f"{n} stations exceeds the single-section limit "
                f"({MAX_STATIONS}); this tool solves one railway section per "
                f"run -- decompose the network geographically and solve "
                f"sections independently, then reconcile boundary frequencies")
        if len(self.palette) > MAX_PALETTE:
            raise ValueError(
                f"palette of {len(self.palette)} pairs exceeds {MAX_PALETTE}; "
                f"reduce the palette size")

    def _window_edges(self):
        e = []
        n = len(self.stations)
        for i in range(n):
            for j in range(i + 1, min(i + self.reuse_window + 1, n)):
                e.append((self.stations[i], self.stations[j]))
        return e

    def _distance_edges(self):
        """Real-RF-range interference graph: stations within rf_range_km interfere."""
        e = []
        S = self.stations
        for i in range(len(S)):
            for j in range(i + 1, len(S)):
                pi, pj = self.positions.get(S[i]), self.positions.get(S[j])
                if pi is not None and pj is not None and abs(pi - pj) <= self.rf_range_km:
                    e.append((S[i], S[j]))
        return e

    def demand(self, s):
        return self.sta_slots.get(s, 0) + self.loco_slots.get(s, 0)

    def allowed_for(self, s):
        return set(self.allowed.get(s, {p.id for p in self.palette}))

    def adjacency(self):
        """Neighbour map from the interference edges (audit DUP-4)."""
        adj = {s: set() for s in self.stations}
        for a, b in self.edges:
            adj[a].add(b); adj[b].add(a)
        return adj


def _mk_palette():
    """Illustrative reference palette (7 duplex pairs; synthetic example data)."""
    return [Pair(1, 400.300, 402.600), Pair(2, 401.200, 403.600),
            Pair(3, 401.400, 404.250), Pair(4, 401.700, 404.525),
            Pair(5, 400.000, 402.175), Pair(6, 400.575, 403.000),
            Pair(7, 401.000, 403.300)]


# ===========================================================================
# IM3 ENGINE  (integer-grid exact)
# ===========================================================================
def _chan(mhz):
    return round(mhz / GRID_MHZ)


def im3_forbidden_colour_sets(palette, f0=None, include_f0=False, orders=("two", "three")):
    """
    Enumerate every NON-DEGENERATE 3rd-order IM relation over the palette's
    carriers and map each to the SET OF COLOURS whose simultaneous use creates
    it:
        two-tone   2a - b = c     (a != b)
        three-tone a + b - c = d   (a, b, c distinct carriers)
    `orders` selects which to include ("two", "three"). Returns minimal
    frozensets of pair-ids. The used colour set is IM3-clean iff no returned set
    is a subset of it. f0 (if included) is an always-on carrier owned by no
    colour, so a relation may be forbidden by as few as one colour.

    The O(C^3) enumeration is memoised on the (carriers, f0, orders) key
    (audit A4); a fresh list copy is returned each call so callers may treat it
    as their own.
    """
    fs_key = tuple(sorted((_chan(p.fS), p.id) for p in palette))
    fm_key = tuple(sorted((_chan(p.fM), p.id) for p in palette))
    f0_chan = _chan(f0) if (f0 is not None and include_f0) else None
    return list(_im3_forbidden_impl((fs_key, fm_key), f0_chan, tuple(orders)))


@lru_cache(maxsize=256)
def _im3_forbidden_impl(carriers_key, f0_chan, orders):
    fs_key, fm_key = carriers_key
    carriers = list(fs_key) + list(fm_key)
    if f0_chan is not None:
        carriers.append((f0_chan, None))

    owner = {}
    for c, o in carriers:
        owner.setdefault(c, set())
        if o is not None:
            owner[c].add(o)
    chanset = set(owner)
    forb = set()

    def add(*cols_iters):
        K = set().union(*cols_iters)
        if K:
            forb.add(frozenset(K))

    if "two" in orders:                       # two-tone  2a - b = c
        for a in chanset:
            for b in chanset:
                if a == b:
                    continue
                if (2 * a - b) in chanset:
                    add(owner[a], owner[b], owner[2 * a - b])

    if "three" in orders:                     # three-tone  a + b - c = d
        cl = sorted(chanset)
        for ai in range(len(cl)):
            for bi in range(ai + 1, len(cl)):
                a, b = cl[ai], cl[bi]
                for c in chanset:
                    if c == a or c == b:
                        continue
                    d = a + b - c
                    if d in chanset:
                        add(owner[a], owner[b], owner[c], owner[d])

    minimal = []
    for K in sorted(forb, key=len):
        if not any(M <= K for M in minimal):
            minimal.append(K)
    return tuple(minimal)


def palette_is_im3_clean(palette, f0=None, include_f0=False):
    """True iff there is NO 3rd-order IM relation among the palette's carriers."""
    fs = im3_forbidden_colour_sets(palette, f0, include_f0)
    return (len(fs) == 0), fs


# ===========================================================================
# LAYER 1 — IM3-FREE PALETTE DESIGN  (Sidon / B2 set)
# ===========================================================================
def is_sidon(channels):
    """True iff integer channel set is Sidon (B2): all pairwise sums distinct
    <=> no 3rd-order IM product lands on a member."""
    sums = set()
    cs = sorted(channels)
    for i in range(len(cs)):
        for j in range(i, len(cs)):
            s = cs[i] + cs[j]
            if s in sums:
                return False
            sums.add(s)
    return True


def greedy_sidon(k, lo, hi):
    """Greedy Sidon set of k channels in [lo, hi], minimising span from lo."""
    chosen, pair_sums = [], set()
    for c in range(lo, hi + 1):
        new = set()
        ok = True
        for x in chosen + [c]:
            s = x + c
            if s in pair_sums or s in new:
                ok = False
                break
            new.add(s)
        if ok:
            chosen.append(c)
            pair_sums |= new
            if len(chosen) == k:
                return chosen
    return None


def design_im3_free_palette(k, band_lo_mhz=406.0, band_hi_mhz=470.0):
    """k IM3-free duplex pairs whose COMBINED {fS}∪{fM} carrier set is Sidon
    (uplink+downlink jointly intermodulation-free)."""
    lo, hi = _chan(band_lo_mhz), _chan(band_hi_mhz)
    comb = greedy_sidon(2 * k, lo, hi)
    if comb is None:
        raise ValueError("No Sidon set of required size fits the band")
    comb.sort()
    return [Pair(i + 1, comb[i] * GRID_MHZ, comb[k + i] * GRID_MHZ)
            for i in range(k)]


# ===========================================================================
# SHARED CP-SAT SLOT-OCCUPANCY MODEL  (audit DUP-1)
# Used by both stagger.stagger_slots and dense_area.solve_joint so the window /
# loco / offset-0 constraints exist in exactly one place.
# ===========================================================================
def build_occupancy(m, prob, usable, gap_slots=None, offset0=False):
    """
    Add per-station slot-occupancy vars/constraints to CP-SAT model `m`:
      - contiguous Stationary Tx window (via a `start` IntVar),
      - non-adjacent Loco slots disjoint from the window,
      - exact demand counts,
      - if `offset0`: every loco slot is >= `gap_slots` markers before the
        station window start (SPN/196 17.14 frame-offset 0).
    Returns (occ, occ_sta, loco, start) dicts keyed (s,t) / s.
    """
    lo, hi = usable[0], usable[-1]
    occ, occ_sta, loco, start = {}, {}, {}, {}
    for s in prob.stations:
        ns, nl = prob.sta_slots.get(s, 0), prob.loco_slots.get(s, 0)
        if ns:
            start[s] = m.NewIntVar(lo, hi - ns + 1, f"start_{s}")
        for t in usable:
            ost = m.NewBoolVar(f"osta_{s}_{t}")
            occ_sta[s, t] = ost
            if ns:
                a = m.NewBoolVar(f"a_{s}_{t}"); b = m.NewBoolVar(f"b_{s}_{t}")
                m.Add(start[s] <= t).OnlyEnforceIf(a)
                m.Add(start[s] > t).OnlyEnforceIf(a.Not())
                m.Add(start[s] + ns - 1 >= t).OnlyEnforceIf(b)
                m.Add(start[s] + ns - 1 < t).OnlyEnforceIf(b.Not())
                m.AddBoolAnd([a, b]).OnlyEnforceIf(ost)
                m.AddBoolOr([a.Not(), b.Not()]).OnlyEnforceIf(ost.Not())
            else:
                m.Add(ost == 0)
        for t in usable:
            lv = m.NewBoolVar(f"loco_{s}_{t}")
            loco[s, t] = lv
            m.Add(lv + occ_sta[s, t] <= 1)
            if ns and offset0 and gap_slots:          # loco >= gap before window
                m.Add(t + gap_slots <= start[s]).OnlyEnforceIf(lv)
            ov = m.NewBoolVar(f"occ_{s}_{t}")
            m.AddMaxEquality(ov, [occ_sta[s, t], lv])
            occ[s, t] = ov
        m.Add(sum(loco[s, t] for t in usable) == nl)
        if prob.loco_nonadjacent:
            for t in usable[:-1]:
                m.Add(loco[s, t] + loco[s, t + 1] <= 1)
    return occ, occ_sta, loco, start


def extract_schedule(solver, prob, usable, occ_sta, occ) -> Schedule:
    """Read a {'station':[...], 'loco':[...]} schedule out of a solved model."""
    sched: Schedule = {}
    for s in prob.stations:
        win = sorted(t for t in usable if solver.Value(occ_sta[s, t]) == 1)
        lc = sorted(t for t in usable
                    if solver.Value(occ[s, t]) == 1 and t not in win)
        sched[s] = {'station': win, 'loco': lc}
    return sched
