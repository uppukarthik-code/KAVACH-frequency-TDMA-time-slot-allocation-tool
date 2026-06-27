#!/usr/bin/env python3
"""
Layer 2b — slot scheduling (Stationary Tx window + non-adjacent Loco slots).

For each station, assign a contiguous Stationary Tx window and non-adjacent Loco
slots, avoiding reserved slots, with the SPN/196 17.14 frame-offset-0 strategy
(loco data >=150-200 ms before the station window) or a compact strategy.

Split out of allocation_solver.py (audit CQ-2). Imports only from `model`;
`allocation_solver` re-exports these names for backward compatibility.
"""
from __future__ import annotations
from typing import Optional

from model import default_gap_slots, Problem, Schedule, SlotEntry


def _contiguous_block(free, length, min_start=None):
    """First run of `length` consecutive markers in `free` (>= min_start)."""
    if length == 0:
        return []
    fs = sorted(t for t in free if min_start is None or t >= min_start)
    for i in range(len(fs) - length + 1):
        if fs[i + length - 1] - fs[i] == length - 1:
            return fs[i:i + length]
    return None


def _all_contiguous_blocks(free, length, min_start=None):
    """Every run of `length` consecutive markers in `free` (>= min_start)."""
    if length == 0:
        yield []
        return
    fs = sorted(t for t in free if min_start is None or t >= min_start)
    for i in range(len(fs) - length + 1):
        if fs[i + length - 1] - fs[i] == length - 1:
            yield fs[i:i + length]


def _pick_nonadj(free, n, nonadj, exclude=()):
    """Earliest `n` markers from free, non-adjacent if required, avoiding exclude."""
    if n == 0:
        return []
    ex, picked = set(exclude), []
    for t in sorted(free):
        if t in ex:
            continue
        if nonadj and any(abs(t - q) == 1 for q in picked):
            continue
        picked.append(t)
        if len(picked) == n:
            break
    return picked if len(picked) == n else None


def _place_compact(free, n_sta, n_loco, nonadj) -> Optional[SlotEntry]:
    """Contiguous Stationary window + non-adjacent loco slots in the remainder.

    Tries EVERY feasible window position (not just the earliest run) so a
    solvable tight frame is never spuriously refused (audit D1): e.g. on
    free=[1,2,3,4,5] with n_sta=1, n_loco=3 the earliest window [1] strands the
    loco slots, but window [2] leaves [1,3,5]. `_pick_nonadj` is earliest-greedy,
    which is optimal for the maximum non-adjacent count on a line, so for each
    window it decides loco-feasibility exactly; enumerating all windows therefore
    makes this placement complete.
    """
    for window in _all_contiguous_blocks(free, n_sta):
        loco = _pick_nonadj(free, n_loco, nonadj, exclude=window)
        if loco is not None:
            return {'station': sorted(window), 'loco': sorted(loco)}
    return None


def _place_offset0(free, n_sta, n_loco, nonadj, gap) -> Optional[SlotEntry]:
    """SPN/196 17.14 frame-offset 0: loco slots EARLY, station window placed
    >= gap markers after the last loco slot (loco data is >=150-200 ms ahead of
    the station transmission, so the station replies with a fresh MA same cycle)."""
    loco = _pick_nonadj(free, n_loco, nonadj)
    if loco is None:
        return None
    if n_sta == 0:
        return {'station': [], 'loco': sorted(loco)}
    min_start = (max(loco) + gap) if loco else None
    window = _contiguous_block([t for t in free if t not in set(loco)],
                               n_sta, min_start)
    if window is None:
        return None
    return {'station': sorted(window), 'loco': sorted(loco)}


def assign_slots(prob: Problem, colour: dict, strategy="offset0",
                 gap_slots=None) -> Schedule:
    """
    For each station assign a contiguous Stationary Tx window + non-adjacent Loco
    slots, disjoint from co-channel in-range neighbours and from reserved slots.
      strategy="offset0"  -> loco slots >=150-200 ms before the station window
                             (SPN/196 17.14 frame-offset 0; per-station fallback
                             to compact if it cannot fit the frame).
      strategy="compact"  -> window first, loco slots packed after.
    Returns id -> {'station': [...], 'loco': [...]}.
    """
    if gap_slots is None:
        gap_slots = default_gap_slots()
    universe = [t for t in range(1, prob.num_slots + 1)
                if t not in prob.reserved_slots]

    sched: Schedule = {}
    # No co-channel slot deconfliction is needed here: assign_colours guarantees
    # that interfering (edge-adjacent) stations never share a pair-colour, so two
    # stations on the same colour are by construction out of RF range and cannot
    # clash in a slot. (A prior per-station `blocked` set was provably always
    # empty -- audit D2.) validate() independently re-checks for any co-channel
    # clash, so the invariant is verified rather than merely assumed.
    for s in sorted(prob.stations, key=lambda s: -prob.demand(s)):
        free = universe
        n_sta = prob.sta_slots.get(s, 0)
        n_loco = prob.loco_slots.get(s, 0)

        placed = None
        if strategy == "offset0":
            placed = _place_offset0(free, n_sta, n_loco, prob.loco_nonadjacent,
                                    gap_slots)
        if placed is None:                       # compact, or offset0 fallback
            placed = _place_compact(free, n_sta, n_loco, prob.loco_nonadjacent)
        if placed is None:
            raise ValueError(f"station {s}: cannot place {n_sta} window + "
                             f"{n_loco} loco slots in the frame")
        sched[s] = placed
    return sched


def frame_offset(prob: Problem, sched: dict, gap_slots=None):
    """Per-station frame offset (SPN/196 17.14): 0 if every loco slot is at least
    `gap_slots` markers before the station window start, else 1."""
    if gap_slots is None:
        gap_slots = default_gap_slots()
    off = {}
    for s in prob.stations:
        loco, win = sched[s]['loco'], sched[s]['station']
        off[s] = 0 if (not loco or not win or
                       (min(win) - max(loco)) >= gap_slots) else 1
    return off
