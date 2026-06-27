#!/usr/bin/env python3
"""
Dense-area / junction extensions for the KAVACH allocation solver.

(a) sectorized_edges():  build the interference graph from DIRECTIONAL antennas
    (per-sector), not omni stations. At a junction, antennas facing different
    branches do not interfere -> edges are cut -> local cliques shrink. This is
    TAN-compliant (it changes the physical RF coupling; cf. TAN 4.3 antenna
    staggering) and feeds straight into the existing solver as `edges`.

(b) solve_joint():  joint frequency x time-slot CELL assignment. Two
    interfering stations may share a frequency PAIR provided their occupied
    slots are disjoint (TDMA reuse), instead of always demanding a new
    frequency. This packs dense clusters onto fewer frequencies.

    *** COMPLIANCE: neighbour frequency reuse (allow_neighbor_reuse=True)
        DEVIATES from TAN 4.11(1) "adjacent stations shall use different
        frequency pairs" and therefore REQUIRES a Project-ISA deviation.
        With allow_neighbor_reuse=False it reduces to the TAN-compliant
        colouring and there is no spectrum saving. ***

Both validated by validate_joint(). solve_joint requires OR-Tools CP-SAT.
"""
from __future__ import annotations
import math

from model import (Problem, im3_forbidden_colour_sets, _HAS_CPSAT,
                   cp_model, cpsat_solve, make_cpsat_solver, build_occupancy,
                   extract_schedule, ORDERS)


# ===========================================================================
# (a) SECTORIZED / DIRECTIONAL INTERFERENCE GRAPH
# ===========================================================================
def _bearing(p, q):
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 360


def _ang_diff(a, b):
    d = abs((a - b) % 360)
    return min(d, 360 - d)


def sectorized_edges(coords, rf_range, antennas=None):
    """
    Build interference edges from directional antennas.
      coords   : id -> (x, y)            same length unit as rf_range
      rf_range : interference radius
      antennas : id -> list[(azimuth_deg, beamwidth_deg)]  (omni if station
                 absent or maps to None)
    Edge (a,b) iff dist(a,b) <= rf_range AND each station has a sector whose
    beam illuminates the other. Omni stations illuminate every direction.
    """
    antennas = antennas or {}

    def illuminates(src, dst):
        secs = antennas.get(src)
        if not secs:                       # omni
            return True
        brg = _bearing(coords[src], coords[dst])
        return any(_ang_diff(brg, az) <= bw / 2.0 for az, bw in secs)

    ids = list(coords)
    edges = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if math.dist(coords[a], coords[b]) <= rf_range \
               and illuminates(a, b) and illuminates(b, a):
                edges.append((a, b))
    return edges


# ===========================================================================
# (b) JOINT FREQUENCY x TIME-SLOT CELL ASSIGNMENT
# ===========================================================================
def solve_joint(prob: Problem, im3_level="full",
                allow_neighbor_reuse=True, time_limit=60):
    """
    Assign each station a pair AND its slots so that interfering stations never
    share the same (pair, slot) cell. Minimises spectrum (distinct pairs).
    Returns a result dict compatible with validate_joint().
    """
    if not _HAS_CPSAT:
        raise RuntimeError("solve_joint requires OR-Tools CP-SAT (pip install ortools)")

    usable = sorted(t for t in range(1, prob.num_slots + 1)
                    if t not in prob.reserved_slots)
    if usable != list(range(usable[0], usable[-1] + 1)):
        raise ValueError("solve_joint assumes a contiguous working frame "
                         "(reserved slots at the ends)")

    orders = ORDERS[im3_level]
    forb = im3_forbidden_colour_sets(prob.palette, prob.f0,
                                     prob.include_f0_in_im3, orders=orders)
    selfblock = {next(iter(K)) for K in forb if len(K) == 1}
    pal_ids = [p.id for p in prob.palette if p.id not in selfblock]
    forb2 = [K for K in forb if len(K) >= 2]

    m = cp_model.CpModel()
    y = {}
    for s in prob.stations:
        allow = prob.allowed_for(s) & set(pal_ids)
        if not allow:
            raise ValueError(f"station {s} has no allowed colour")
        y[s] = {p: m.NewBoolVar(f"y_{s}_{p}") for p in allow}
        m.Add(sum(y[s].values()) == 1)
    used = {p: m.NewBoolVar(f"u_{p}") for p in pal_ids}
    for s in prob.stations:
        for p, v in y[s].items():
            m.Add(used[p] >= v)
    for K in forb2:
        if all(p in used for p in K):
            m.Add(sum(used[p] for p in K) <= len(K) - 1)

    # slot occupancy per station (shared builder; offset-0 NOT enforced for the
    # joint / neighbour-reuse model)
    occ, occ_sta, loco, start = build_occupancy(m, prob, usable)

    # interference
    if allow_neighbor_reuse:
        cell = {}                              # (s,p,t) -> bool
        in_edge = set()
        for a, b in prob.edges:
            in_edge.add(a); in_edge.add(b)
        for s in in_edge:
            for p in y[s]:
                for t in usable:
                    cv = m.NewBoolVar(f"cell_{s}_{p}_{t}")
                    m.Add(cv <= y[s][p]); m.Add(cv <= occ[s, t])
                    m.Add(cv >= y[s][p] + occ[s, t] - 1)
                    cell[s, p, t] = cv
        for a, b in prob.edges:
            for p in pal_ids:
                if p in y[a] and p in y[b]:
                    for t in usable:
                        m.Add(cell[a, p, t] + cell[b, p, t] <= 1)
    else:
        for a, b in prob.edges:               # TAN-compliant: neighbours differ
            for p in pal_ids:
                if p in y[a] and p in y[b]:
                    m.Add(y[a][p] + y[b][p] <= 1)

    m.Minimize(sum(used.values()))
    solver = make_cpsat_solver(time_limit)
    cpsat_solve(solver, m, "joint assignment")

    colour = {s: next(p for p, v in y[s].items() if solver.Value(v) == 1)
              for s in prob.stations}
    sched = extract_schedule(solver, prob, usable, occ_sta, occ)
    res = {'colour': colour,
           'used_pairs': {p for p in pal_ids if solver.Value(used[p]) == 1},
           'schedule': sched, 'spectrum': sum(solver.Value(used[p]) for p in pal_ids),
           'im3_level': im3_level, 'allow_neighbor_reuse': allow_neighbor_reuse}
    res['errors'] = validate_joint(prob, res, im3_orders=orders)
    return res


def validate_joint(prob: Problem, result, im3_orders=("two", "three")):
    """Independent verifier for joint plans: cell-disjointness instead of
    neighbour-different colouring."""
    errs = []
    colour, sched = result['colour'], result['schedule']
    occ = {s: set(sched[s]['station']) | set(sched[s]['loco']) for s in prob.stations}
    for a, b in prob.edges:
        if colour[a] == colour[b]:
            clash = occ[a] & occ[b]
            if clash:
                errs.append(f"cell clash {a}-{b} (pair {colour[a]}) slots {sorted(clash)}")
    pal = {p.id: p for p in prob.palette}
    upal = [pal[c] for c in set(colour.values())]
    for K in im3_forbidden_colour_sets(upal, prob.f0, prob.include_f0_in_im3,
                                       orders=im3_orders):
        if K <= set(colour.values()):
            errs.append(f"IM3: used colours {tuple(sorted(K))} form an IM relation")
    for s in prob.stations:
        sl = sched[s]
        if len(sl['station']) != prob.sta_slots.get(s, 0):
            errs.append(f"{s}: station-slot count")
        if len(sl['loco']) != prob.loco_slots.get(s, 0):
            errs.append(f"{s}: loco-slot count")
        if set(sl['station'] + sl['loco']) & prob.reserved_slots:
            errs.append(f"{s}: reserved-slot use")
        ls = sorted(sl['loco'])
        if prob.loco_nonadjacent and any(b - a == 1 for a, b in zip(ls, ls[1:])):
            errs.append(f"{s}: adjacent loco slots {ls}")
        win = sorted(sl['station'])
        if win and win[-1] - win[0] != len(win) - 1:
            errs.append(f"{s}: station window not contiguous {win}")
        if set(sl['station']) & set(sl['loco']):
            errs.append(f"{s}: window/loco overlap")
    return errs


def compliance_note_joint(result):
    """Compliance line for a joint result (TAN 4.11(1) implication)."""
    if result.get('allow_neighbor_reuse'):
        return ("TAN 4.11(1) - adjacent stations different pair",
                "DEVIATION - neighbour frequency reuse via time-slots; "
                "REQUIRES Project-ISA deviation with hazard mitigation")
    return ("TAN 4.11(1) - adjacent stations different pair", "PASS")


# ===========================================================================
# DEMO — junction (sectorized) + dense cluster (time-reuse)
# ===========================================================================
def _demo():
    from allocation_solver import _mk_palette, solve, KAVACH_F0
    pal = _mk_palette()

    print("== (a) JUNCTION with directional antennas ==")
    # Junction J, 3 branches of 2 stations; branch first-stations are close
    # enough to interfere omni, but each station's antennas point ALONG its own
    # branch (real KAVACH layout) so cross-branch beams miss each other.
    coords = {"J": (0, 0),
              "A1": (0.6, 0), "A2": (1.2, 0),
              "B1": (-0.3, 0.52), "B2": (-0.6, 1.04),
              "C1": (-0.3, -0.52), "C2": (-0.6, -1.04)}
    antennas = {"J": [(0, 60), (120, 60), (240, 60)],
                "A1": [(0, 60), (180, 60)], "A2": [(0, 60), (180, 60)],
                "B1": [(120, 60), (300, 60)], "B2": [(120, 60), (300, 60)],
                "C1": [(240, 60), (60, 60)], "C2": [(240, 60), (60, 60)]}
    omni = sectorized_edges(coords, 1.3)               # cross-branch stations interfere
    sect = sectorized_edges(coords, 1.3, antennas)     # directional beams cut cross-branch
    print(f"  omni edges      : {len(omni)}  (cross-branch coupling near junction)")
    print(f"  sectorized edges: {len(sect)}  (cross-branch edges cut)")
    ids = list(coords)
    for label, e in (("omni", omni), ("sectorized", sect)):
        prob = Problem(ids, {i: 4 for i in ids}, {i: 3 for i in ids}, pal,
                       edges=e, f0=KAVACH_F0)
        r = solve(prob, im3_level="two_tone")
        print(f"  {label:11}: spectrum = {r['spectrum']} pairs, errors={r['errors']}")
    print()

    print("== (b) DENSE CLUSTER: frequency vs time-slot reuse ==")
    # 6 mutually-interfering stations (urban node), modest demand each.
    ids = [f"U{i}" for i in range(6)]
    edges = [(ids[i], ids[j]) for i in range(6) for j in range(i + 1, 6)]
    prob = Problem(ids, {i: 3 for i in ids}, {i: 2 for i in ids}, pal,
                   edges=edges, f0=KAVACH_F0)
    comp = solve(prob, im3_level="two_tone")
    print(f"  TAN-compliant (neighbours differ): {comp['spectrum']} pairs")
    joint = solve_joint(prob, im3_level="two_tone", allow_neighbor_reuse=True)
    clause, status = compliance_note_joint(joint)
    print(f"  joint time-reuse                 : {joint['spectrum']} pairs, "
          f"errors={joint['errors']}")
    print(f"  -> {clause}: {status}")


if __name__ == "__main__":
    _demo()
