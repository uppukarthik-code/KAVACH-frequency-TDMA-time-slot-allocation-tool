#!/usr/bin/env python3
"""
Time-domain interference minimisation (slot staggering).

Frequency selection removes IM in the FREQUENCY domain. Whatever third-order IM
products remain (the ISA residuals) only actually FORM when the carriers that
make them are on-air in the SAME time slot at mutually in-range stations. This
module rearranges the time slots (keeping every hard rule) to make those
carriers coincide as rarely as the 44-slot frame allows:

    eliminate the maximum interference; what's left is the least possible.

  im_station_tuples()      : geographic groups (one station per colour of an IM
                             relation, all mutually in RF range) where a residual
                             product can physically form.
  count_im_coincidence()   : independent counter of (group x slot) simultaneities
                             for ANY schedule (default or staggered).
  stagger_slots()          : CP-SAT slot placement minimising those coincidences,
                             subject to demand, contiguous window, non-adjacent
                             loco, reserved slots and SPN/196 17.14 offset-0.

Requires OR-Tools CP-SAT for the optimiser; the counter is pure-Python.
"""
from __future__ import annotations
from collections import defaultdict

from model import (Problem, im3_forbidden_colour_sets, default_gap_slots,
                   _HAS_CPSAT, cp_model, cpsat_solve, make_cpsat_solver,
                   build_occupancy, extract_schedule, ORDERS)


def im_station_tuples(prob: Problem, colour: dict, im_level="full"):
    """Groups of stations (one per colour of a residual IM relation) that are
    mutually within RF range -> where the product can actually form."""
    used = set(colour.values())
    forb = im3_forbidden_colour_sets(prob.palette, prob.f0,
                                     prob.include_f0_in_im3, ORDERS[im_level])
    rels = [K for K in forb if K <= used]
    adj = prob.adjacency()
    by_colour = defaultdict(list)
    for s in prob.stations:
        by_colour[colour[s]].append(s)
    tuples = []
    for K in rels:
        cols = sorted(K)
        # Build mutually-in-range combos (one station per colour) INCREMENTALLY,
        # extending a partial tuple only with a station adjacent to all already
        # chosen. This prunes non-clique partials during generation instead of
        # materialising the full Cartesian product and filtering after it
        # (audit A6); the final partials are exactly the in-range combos.
        partial = [()]
        for c in cols:
            partial = [combo + (s,) for combo in partial
                       for s in by_colour[c]
                       if all(s in adj[a] for a in combo)]
        for combo in partial:
            tuples.append((K, combo))
    return tuples


def count_im_coincidence(prob, colour, sched, im_level="full"):
    """Total (group x slot) IM coincidences and a per-relation breakdown for a
    given schedule. Lower is better; 0 means no residual product can form."""
    tuples = im_station_tuples(prob, colour, im_level)
    occ = {s: set(sched[s]['station']) | set(sched[s]['loco'])
           for s in prob.stations}
    total, per_rel = 0, defaultdict(int)
    for K, combo in tuples:
        common = set.intersection(*[occ[s] for s in combo]) if combo else set()
        total += len(common)
        per_rel[tuple(sorted(K))] += len(common)
    return total, dict(per_rel)


def stagger_slots(prob: Problem, colour: dict, im_level="full",
                  gap_slots=None, time_limit=60):
    """Place slots to MINIMISE IM coincidence (time-domain interference) while
    honouring demand, contiguous window, non-adjacent loco, reserved slots and
    SPN/196 17.14 frame-offset-0. Returns id -> {'station':[...],'loco':[...]}."""
    if not _HAS_CPSAT:
        raise RuntimeError("stagger_slots requires OR-Tools CP-SAT")
    if gap_slots is None:
        gap_slots = default_gap_slots()
    usable = sorted(t for t in range(1, prob.num_slots + 1)
                    if t not in prob.reserved_slots)

    m = cp_model.CpModel()
    occ, occ_sta, loco, start = build_occupancy(m, prob, usable, gap_slots,
                                                offset0=True)

    # objective: minimise IM coincidences (all carriers of a relation active together)
    pen = []
    for K, combo in im_station_tuples(prob, colour, im_level):
        k = len(combo)
        for t in usable:
            c = m.NewBoolVar(f"co_{'_'.join(map(str, combo))}_{t}")
            m.Add(c >= sum(occ[s, t] for s in combo) - (k - 1))
            pen.append(c)
    m.Minimize(sum(pen) if pen else 0)

    solver = make_cpsat_solver(time_limit)
    cpsat_solve(solver, m, "stagger")
    return extract_schedule(solver, prob, usable, occ_sta, occ)


def _demo():
    from allocation_solver import (_mk_palette, Problem, solve, KAVACH_F0,
                                   frame_offset)
    rows = [(10001, 7, 5), (10002, 7, 5), (10003, 7, 5), (10004, 11, 11),
            (10005, 8, 6), (10006, 12, 13), (10007, 8, 6), (10008, 10, 10),
            (10009, 7, 5), (10010, 7, 4), (10011, 10, 10), (10012, 7, 4),
            (10013, 6, 2), (10014, 10, 10), (10015, 8, 6), (10016, 10, 10),
            (10017, 5, 3), (10018, 4, 2), (10019, 7, 7), (10020, 10, 10),
            (10021, 8, 6), (10022, 7, 4)]
    pal = _mk_palette()
    prob = Problem([r[0] for r in rows], {r[0]: r[1] for r in rows},
                   {r[0]: r[2] for r in rows}, pal, reuse_window=4, f0=KAVACH_F0)
    res = solve(prob, im3_level="two_tone")
    LV = "full"
    before, bb = count_im_coincidence(prob, res['colour'], res['schedule'], LV)
    sched2 = stagger_slots(prob, res['colour'], im_level=LV)
    after, ab = count_im_coincidence(prob, res['colour'], sched2, LV)
    fo = frame_offset(prob, sched2)
    print(f"colours used: {sorted(res['used_pairs'])}")
    print(f"three-tone IM-forming in-range station groups: "
          f"{len(im_station_tuples(prob, res['colour'], LV))}")
    print(f"BEFORE (default offset-0): {before} IM coincidence slot-events  {bb}")
    print(f"AFTER  (staggered)       : {after} IM coincidence slot-events  {ab}")
    print(f"frame-offset-0 preserved : {sum(v == 0 for v in fo.values())}/{len(fo)}")
    print("=> " + ("residual three-tone products TIME-ELIMINATED"
                   if after == 0 else f"minimised to {after} (least achievable)"))


if __name__ == "__main__":
    _demo()
