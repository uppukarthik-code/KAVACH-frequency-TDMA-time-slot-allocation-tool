#!/usr/bin/env python3
"""
KAVACH Frequency / Time-Slot Allocation — reference optimization solver
=======================================================================

Implements the two-layer framework in `optimization-framework.md`:

  Layer 1 (channel design):  build an IM3-free palette (Sidon / B2 set) so that
                             third-order intermodulation is impossible by
                             construction.
  Layer 2 (assignment):      list multi-colouring of the station interference
                             graph that is spectrum-minimal and IM3-clean as a
                             hypergraph, then time-slot scheduling.

Each duplex frequency PAIR is treated as ONE ATOMIC COLOUR (current KAVACH
practice): colour `p` owns carriers {fS_p, fM_p}; the control/emergency carrier
f0 is shared and (by default) time-separated in slots P47-P70.

This module is now the PIPELINE + a backward-compatible FACADE (audit CQ-2):
the building blocks live in `model` (data model + IM3 engine + Sidon + shared
CP-SAT infra), `colour` (Layer 2a colour assignment), `slots` (Layer 2b slot
scheduling) and `report` (allocation table + compliance + justification). They
are all re-exported here so existing `import allocation_solver` / `from
allocation_solver import ...` call sites keep working unchanged. `validate`,
`solve` and `solve_compliant` are defined here.

Solvers: OR-Tools CP-SAT if available, else an exact pure-Python branch & bound.
Run the worked SEC-A-SEC-B example:  python3 allocation_solver.py
"""

from __future__ import annotations
import warnings

import stagger

# --- Data model, IM3 engine, Sidon design and shared CP-SAT helpers (model.py,
#     the dependency root) ---
from model import (                                  # noqa: F401  (re-exported API)
    cp_model, _HAS_CPSAT, GRID_MHZ, SOLVER_TIME_S, MARKER_MS, KAVACH_F0, ORDERS,
    cpsat_solve, make_cpsat_solver, default_gap_slots, Pair, Problem,
    _mk_palette, _chan, im3_forbidden_colour_sets, palette_is_im3_clean,
    is_sidon, design_im3_free_palette,
    SlotEntry, Schedule, SolveResult, CompliantResult,
)
# --- Layer 2a: colour assignment (colour.py) ---
from colour import (                                 # noqa: F401  (re-exported API)
    assign_colours, _assign_cpsat, _colourable, _assign_backtrack,
)
# --- Layer 2b: slot scheduling (slots.py) ---
from slots import (                                  # noqa: F401  (re-exported API)
    _contiguous_block, _all_contiguous_blocks, _pick_nonadj, _place_compact,
    _place_offset0, assign_slots, frame_offset,
)
# --- Reporting (report.py) ---
from report import (                                 # noqa: F401  (re-exported API)
    _fmt_markers, allocation_table, print_allocation_table, _ALLOC_FIELDS,
    write_allocation_csv, compliance_report, justify_changes, slot_demand_table,
)


# ===========================================================================
# VALIDATION
# ===========================================================================
def validate(prob: Problem, colour, sched, im3_orders=("two", "three")):
    errs = []
    for a, b in prob.edges:
        if colour[a] == colour[b]:
            errs.append(f"adjacency: {a},{b} share colour {colour[a]}")
    pal = {p.id: p for p in prob.palette}
    upal = [pal[c] for c in set(colour.values())]
    fs = im3_forbidden_colour_sets(upal, prob.f0, prob.include_f0_in_im3,
                                   orders=im3_orders)
    for K in fs:
        if K <= set(colour.values()):
            errs.append(f"IM3: used colours {tuple(sorted(K))} form an IM relation")
    adj = {s: set() for s in prob.stations}
    for a, b in prob.edges:
        adj[a].add(b); adj[b].add(a)
    for s in prob.stations:
        sl = sched[s]
        allslots = sl['station'] + sl['loco']
        if len(sl['station']) != prob.sta_slots.get(s, 0):
            errs.append(f"{s}: station-slot count")
        if len(sl['loco']) != prob.loco_slots.get(s, 0):
            errs.append(f"{s}: loco-slot count")
        if set(allslots) & prob.reserved_slots:
            errs.append(f"{s}: uses reserved slot")
        ls = sorted(sl['loco'])
        if prob.loco_nonadjacent and any(b - a == 1 for a, b in zip(ls, ls[1:])):
            errs.append(f"{s}: adjacent loco slots {ls}")
        win = sorted(sl['station'])
        if win and win[-1] - win[0] != len(win) - 1:
            errs.append(f"{s}: station window not contiguous {win}")
        if set(sl['station']) & set(sl['loco']):
            errs.append(f"{s}: station window overlaps loco slots "
                        f"{sorted(set(sl['station']) & set(sl['loco']))}")
        for n in adj[s]:
            if colour[n] == colour[s]:
                clash = set(allslots) & set(sched[n]['station'] + sched[n]['loco'])
                if clash:
                    errs.append(f"co-channel slot clash {s}-{n}: {sorted(clash)}")
    return errs


# ===========================================================================
# PIPELINE
# ===========================================================================
def solve(prob: Problem, im3_level="full", maximize_separation=True,
          slot_strategy="offset0", gap_slots=None) -> SolveResult:
    orders = ORDERS[im3_level]
    if gap_slots is None:
        gap_slots = default_gap_slots()
    colour, used = assign_colours(prob, im3_level, maximize_separation)
    sched = assign_slots(prob, colour, strategy=slot_strategy, gap_slots=gap_slots)
    errs = validate(prob, colour, sched, im3_orders=orders)
    return {'colour': colour, 'used_pairs': used, 'schedule': sched,
            'spectrum': len(used), 'errors': errs, 'im3_level': im3_level,
            'slot_strategy': slot_strategy, 'gap_slots': gap_slots}


def solve_compliant(prob: Problem, slot_strategy="offset0", gap_slots=None,
                    time_stagger=True) -> CompliantResult:
    """
    Full pipeline with graceful IM3 fallback:
      1. try a fully IM3-clean colouring (2-tone + 3-tone) on the given palette,
      2. else a two-tone-free colouring (3-tone residuals minimised in time),
      3. else report that only a fresh Sidon palette can be fully clean.
    Colour-infeasibility (palette/IM3) and slot-infeasibility (frame capacity)
    are reported separately so the diagnostic is never misleading.

    Slots use the SPN/196 17.14 frame-offset-0 strategy, and by default are also
    TIME-STAGGERED so the carriers of any residual three-tone IM relation never
    share a slot at in-range stations (the product then cannot form). Staggering
    needs CP-SAT; without it the pipeline falls back to the plain offset-0
    placement and the residuals are reported for the ISA.
    """
    if not prob.stations:
        raise ValueError("no stations to allocate (empty problem)")
    if not prob.palette:
        raise ValueError("empty frequency palette")
    if gap_slots is None:
        gap_slots = default_gap_slots()
    # Colour assignment is MANDATORY. The ladder degrades only on IM3
    # infeasibility (ValueError); a CP-SAT/backtracking TIME-OUT (TimeoutError)
    # is deliberately NOT caught here, so it surfaces as a clear "increase the
    # time budget" error instead of being mis-reported as IM3 infeasibility
    # (audit B1). This is the intended asymmetry with staggering below, which is
    # an enhancement and DOES degrade on a time-out.
    colour = used = level = None
    for lvl in ("full", "two_tone"):
        try:
            colour, used = assign_colours(prob, im3_level=lvl)
            level = lvl
            break
        except ValueError:
            continue
    if colour is None:
        raise ValueError("no IM3-acceptable colouring exists on this palette at "
                         "the given interference radius; redesign the palette "
                         "(design_im3_free_palette / Sidon) or relax the radius")
    orders = ORDERS[level]

    sched = None
    staggered = False                                 # did staggering ACTUALLY run?
    if time_stagger and _HAS_CPSAT:
        try:                                          # minimise 3-tone coincidences
            sched = stagger.stagger_slots(prob, colour, im_level="full",
                                       gap_slots=gap_slots)
            staggered = True
        except (RuntimeError, ValueError, ImportError, TimeoutError) as exc:
            # staggering is an enhancement: a failure OR a time-out (TimeoutError
            # from cpsat_solve) degrades to plain offset-0 slots (audit B1).
            warnings.warn(f"time-domain staggering failed ({exc}); falling back "
                          f"to plain offset-0 slots, residual 3-tone IM -> ISA",
                          RuntimeWarning)
            sched = None
    if sched is None:                                 # fallback: plain offset-0
        sched = assign_slots(prob, colour, strategy=slot_strategy, gap_slots=gap_slots)
    errs = validate(prob, colour, sched, im3_orders=orders)

    imco = None
    try:
        imco = stagger.count_im_coincidence(prob, colour, sched, "full")
    except ImportError:
        imco = None
    return {'colour': colour, 'used_pairs': used, 'schedule': sched,
            'spectrum': len(used), 'errors': errs, 'im3_level': level,
            'slot_strategy': slot_strategy, 'gap_slots': gap_slots,
            'staggered': staggered,
            'im_coincidence': imco,
            'im3_note': ("fully IM3-clean (2-tone + 3-tone)" if level == "full"
                         else "two-tone-free; residual 3-tone products minimised "
                         "in the time domain (any remainder -> ISA)")}


# ===========================================================================
# WORKED EXAMPLE — SEC-A-SEC-B
# ===========================================================================
def _ajj_ru_demo():
    palette = _mk_palette()
    f0 = KAVACH_F0
    # (station_id, n_station_slots, n_loco_slots) from the SEC-A-SEC-B sheet
    rows = [
        (10001, 7, 5), (10002, 7, 5), (10003, 7, 5), (10004, 11, 11),
        (10005, 8, 6), (10006, 12, 13), (10007, 8, 6), (10008, 10, 10),
        (10009, 7, 5), (10010, 7, 4), (10011, 10, 10), (10012, 7, 4),
        (10013, 6, 2), (10014, 10, 10), (10015, 8, 6), (10016, 10, 10),
        (10017, 5, 3), (10018, 4, 2), (10019, 7, 7), (10020, 10, 10),
        (10021, 8, 6), (10022, 7, 4),
    ]
    print(f"CP-SAT backend available: {_HAS_CPSAT}\n")

    print("== Layer-1 check: is the legacy 7-pair palette IM3-clean? ==")
    clean, fs = palette_is_im3_clean(palette, f0, include_f0=False)
    print(f"  fully clean (working carriers): {clean}")
    print(f"  forbidden colour-relations: {[tuple(sorted(k)) for k in fs]}\n")

    print("== Solve: spectrum-minimal + wider-separation, IM3-aware plan ==")
    # Realistic reuse radius (~10-15 km ≈ 4 stations) and real KAVACH frame.
    prob = Problem([r[0] for r in rows], {r[0]: r[1] for r in rows},
                   {r[0]: r[2] for r in rows}, palette, reuse_window=4,
                   f0=f0, include_f0_in_im3=False)
    res = solve_compliant(prob)
    print(f"  IM3: {res['im3_note']}")
    print(f"  validation: "
          f"{'PASS — zero interference' if not res['errors'] else res['errors']}\n")

    print("== FREQUENCY & TIME-SLOT ALLOCATION AT EVERY STATION ==")
    print_allocation_table(prob, res)
    print("\n== COMPLIANCE (RDSO TAN 4.11 + SPN/196) ==")
    for clause, status in compliance_report(prob, res):
        print(f"  [{status.split()[0]:4}] {clause}: {status}")
    write_allocation_csv("allocation_SEC-A-SEC-B.csv", prob, res)
    print()

    print("== Layer-1 alternative: design a fresh Sidon (IM3-free) palette ==")
    fresh = design_im3_free_palette(5, 406.0, 470.0)
    comb = [_chan(p.fS) for p in fresh] + [_chan(p.fM) for p in fresh]
    print(f"  combined channels Sidon? {is_sidon(comb)}")
    for p in fresh:
        print(f"    pair {p.id}: fS={p.fS:.3f}  fM={p.fM:.3f}")
    print(f"  fresh palette IM3-clean: {palette_is_im3_clean(fresh)[0]}")


if __name__ == "__main__":
    _ajj_ru_demo()
