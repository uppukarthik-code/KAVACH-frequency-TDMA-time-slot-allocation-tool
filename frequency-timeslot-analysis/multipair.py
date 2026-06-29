#!/usr/bin/env python3
"""
Multi-pair-per-station support.

The base allocator gives every station ONE frequency pair, whose 44 working
markers (P2..P45) cap a station's slot demand. A very busy terminal whose demand
exceeds 44 markers therefore needs MORE THAN ONE pair. This module makes that
automatic: an over-capacity station is split into K = ceil(demand/44) sub-units,
its locos distributed across them, each sub-unit allocated its OWN pair + slots
by the unchanged allocator, then the sub-units are reported together as one
station "using pairs {A, B, ...}".

Sub-units of one station are at the same location, so they (a) mutually interfere
-> get DISTINCT pairs, and (b) inherit every interference edge of the parent.

This is a thin pre/post wrapper around the existing pipeline; the core
colouring/scheduling/IM3 logic is untouched and the 44-marker cap is the spec
ceiling (SPN/196: "Max 44 locos per Stationary KAVACH, UHF").
"""
from __future__ import annotations
import math

from model import Problem


def usable_markers(prob: Problem) -> int:
    """Working markers available on one pair (frame minus reserved)."""
    return len([t for t in range(1, prob.num_slots + 1)
                if t not in prob.reserved_slots])


def _even_split(value: int, k: int) -> list:
    """Split `value` into k as-equal-as-possible non-negative integers."""
    base, extra = divmod(value, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def plan_split(sta_slots: dict, loco_slots: dict, capacity: int = 44) -> dict:
    """{station: (K, [sta_k], [loco_k])} for stations whose demand exceeds one
    pair. The locos are distributed evenly so each sub-unit fits `capacity`."""
    plan = {}
    for s in sta_slots:
        ns, nl = sta_slots.get(s, 0), loco_slots.get(s, 0)
        if ns + nl <= capacity:
            continue
        k = math.ceil((ns + nl) / capacity)
        while True:                              # bump K if rounding overflows
            sp, lp = _even_split(ns, k), _even_split(nl, k)
            if all(a + b <= capacity for a, b in zip(sp, lp)):
                break
            k += 1
        plan[s] = (k, sp, lp)
    return plan


def expand_problem(prob: Problem, capacity: int = None):
    """Return (expanded_problem, mapping) where every over-capacity station is
    replaced by sub-units `"<id>#1".."<id>#K"`. mapping: sub-unit -> original id.
    Non-split stations map to themselves. If nothing needs splitting, returns the
    original problem unchanged."""
    cap = capacity if capacity is not None else usable_markers(prob)
    plan = plan_split(prob.sta_slots, prob.loco_slots, cap)
    if not plan:
        return prob, {s: s for s in prob.stations}

    sub_of, mapping = {}, {}
    ids, sta, loco = [], {}, {}
    for s in prob.stations:
        if s in plan:
            k, sp, lp = plan[s]
            subs = [f"{s}#{i + 1}" for i in range(k)]
        else:
            subs, sp, lp = [s], [prob.sta_slots.get(s, 0)], [prob.loco_slots.get(s, 0)]
        sub_of[s] = subs
        for su, a, b in zip(subs, sp, lp):
            ids.append(su); mapping[su] = s; sta[su] = a; loco[su] = b

    edges = set()
    for a, b in prob.edges:                       # inherit parent interference
        for sa in sub_of[a]:
            for sb in sub_of[b]:
                edges.add((sa, sb))
    for subs in sub_of.values():                  # sub-units of one station: clique
        for i in range(len(subs)):
            for j in range(i + 1, len(subs)):
                edges.add((subs[i], subs[j]))

    allowed = {}
    for s, subs in sub_of.items():
        if s in prob.allowed:
            for su in subs:
                allowed[su] = set(prob.allowed[s])

    expanded = Problem(ids, sta, loco, prob.palette, edges=list(edges),
                       f0=prob.f0, include_f0_in_im3=prob.include_f0_in_im3,
                       num_slots=prob.num_slots,
                       reserved_slots=set(prob.reserved_slots),
                       loco_nonadjacent=prob.loco_nonadjacent, allowed=allowed)
    return expanded, mapping


def solve_multipair(prob: Problem, capacity: int = None, **kw):
    """Like allocation_solver.solve_compliant, but over-capacity stations are
    auto-split across pairs. Returns (result, problem_used). `result` carries two
    extra keys:
        'mapping'        sub-unit id -> original station id
        'station_pairs'  original station id -> sorted list of pair ids it uses
    When no station is over capacity this is exactly solve_compliant on `prob`."""
    import allocation_solver as A
    expanded, mapping = expand_problem(prob, capacity)
    res = A.solve_compliant(expanded, **kw)
    grouped = {}
    for su, pair in res["colour"].items():
        grouped.setdefault(mapping[su], set()).add(pair)
    res["mapping"] = mapping
    res["station_pairs"] = {o: sorted(p) for o, p in grouped.items()}
    return res, expanded


def _demo():
    import allocation_solver as A
    pal = A._mk_palette()
    # one very busy terminal (demand 23+30 = 53 > 44) plus two normal stations
    prob = A.Problem([1, 2, 3], {1: 23, 2: 6, 3: 6}, {1: 30, 2: 4, 3: 4}, pal,
                     reuse_window=2, f0=A.KAVACH_F0)
    res, used = solve_multipair(prob)
    print("station_pairs (original id -> pairs used):")
    for o, ps in sorted(res["station_pairs"].items(), key=lambda kv: str(kv[0])):
        tag = "  <- split across pairs" if len(ps) > 1 else ""
        print(f"  station {o}: pairs {ps}{tag}")
    print(f"spectrum = {res['spectrum']} pairs, errors = {res['errors']}")
    print(f"sub-units: {sorted(used.stations, key=str)}")


if __name__ == "__main__":
    _demo()
