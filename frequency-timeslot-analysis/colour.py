#!/usr/bin/env python3
"""
Layer 2a — atomic pair-colour assignment (spectrum-minimal, IM3-clean).

One atomic frequency-pair colour per station; adjacent stations differ; the used
colour set is IM3-clean (as a hypergraph); minimise the number of distinct
colours (spectrum), then (TAN 4.11) maximise the minimum adjacent-station
frequency separation. Uses OR-Tools CP-SAT when available, else an exact
pure-Python branch & bound.

Split out of allocation_solver.py (audit CQ-2). Imports only from `model` (the
dependency root); `allocation_solver` re-exports these names for backward
compatibility.
"""
from __future__ import annotations
import itertools
import time

from model import (cp_model, _HAS_CPSAT, SOLVER_TIME_S, ORDERS, cpsat_solve,
                   make_cpsat_solver, _chan, im3_forbidden_colour_sets, Problem)


def assign_colours(prob: Problem, im3_level="full", maximize_separation=True):
    """
    One atomic pair-colour per station; adjacent differ; used set IM3-clean;
    minimise #distinct colours (spectrum), then (TAN 4.11) maximise the minimum
    adjacent-station frequency separation.
      im3_level: "full" = 2-tone + 3-tone forbidden; "two_tone" = only 2-tone
                 (3-tone residuals deferred to the ISA); "none" = ignore IM3.
    Returns (colour, used_pairs).
    """
    if not prob.stations:
        raise ValueError("no stations to allocate (empty problem)")
    if not prob.palette:
        raise ValueError("empty frequency palette")
    orders = ORDERS[im3_level]
    forb = im3_forbidden_colour_sets(prob.palette, prob.f0,
                                     prob.include_f0_in_im3, orders=orders)
    selfblock = {next(iter(K)) for K in forb if len(K) == 1}
    pal_ids = [p.id for p in prob.palette if p.id not in selfblock]
    if not pal_ids:
        raise ValueError("no usable frequency pairs after IM3 self-block filter")
    forb2 = [K for K in forb if len(K) >= 2]
    if _HAS_CPSAT:
        return _assign_cpsat(prob, pal_ids, forb2, maximize_separation)
    return _assign_backtrack(prob, pal_ids, forb2)


def _assign_cpsat(prob, pal_ids, forb2, maximize_separation=True):
    chan = {p.id: _chan(p.fS) for p in prob.palette}

    def build():
        m = cp_model.CpModel()
        col = {}
        for s in prob.stations:
            allow = prob.allowed_for(s) & set(pal_ids)
            if not allow:
                raise ValueError(f"station {s} has no allowed colour")
            col[s] = {p: m.NewBoolVar(f"x_{s}_{p}") for p in allow}
            m.Add(sum(col[s].values()) == 1)
        used = {p: m.NewBoolVar(f"u_{p}") for p in pal_ids}
        for s in prob.stations:
            for p, v in col[s].items():
                m.Add(used[p] >= v)
        for a, b in prob.edges:
            for p in pal_ids:
                if p in col[a] and p in col[b]:
                    m.Add(col[a][p] + col[b][p] <= 1)
        for K in forb2:
            if all(p in used for p in K):
                m.Add(sum(used[p] for p in K) <= len(K) - 1)
        return m, col, used

    # Phase 1: minimise spectrum
    m, col, used = build()
    m.Minimize(sum(used.values()))
    s1 = make_cpsat_solver(SOLVER_TIME_S)
    cpsat_solve(s1, m, "colour assignment")
    kstar = int(round(s1.ObjectiveValue()))

    if not maximize_separation:
        colour = {s: next(p for p, v in col[s].items() if s1.Value(v) == 1)
                  for s in prob.stations}
        return colour, {p for p in pal_ids if s1.Value(used[p]) == 1}

    # Phase 2: fix spectrum, maximise minimum adjacent separation (TAN 4.11)
    m, col, used = build()
    m.Add(sum(used.values()) <= kstar)
    chvals = list(chan.values())
    lo, hi = min(chvals), max(chvals)
    chvar = {}
    for s in prob.stations:
        cs = m.NewIntVar(lo, hi, f"ch_{s}")
        m.Add(cs == sum(chan[p] * v for p, v in col[s].items()))
        chvar[s] = cs
    sep_min = m.NewIntVar(0, hi - lo, "sep_min")
    for a, b in prob.edges:
        d = m.NewIntVar(-(hi - lo), hi - lo, f"d_{a}_{b}")
        m.Add(d == chvar[a] - chvar[b])
        ad = m.NewIntVar(0, hi - lo, f"ad_{a}_{b}")
        m.AddAbsEquality(ad, d)
        m.Add(sep_min <= ad)
    m.Maximize(sep_min)
    s2 = make_cpsat_solver(SOLVER_TIME_S)
    cpsat_solve(s2, m, "separation phase")
    colour = {s: next(p for p, v in col[s].items() if s2.Value(v) == 1)
              for s in prob.stations}
    return colour, {p for p in pal_ids if s2.Value(used[p]) == 1}


def _colourable(prob, subset, check_time=None):
    adj = {s: set() for s in prob.stations}
    for a, b in prob.edges:
        adj[a].add(b); adj[b].add(a)
    order = sorted(prob.stations, key=lambda s: -len(adj[s]))
    asg = {}

    def bt(i):
        if check_time is not None:
            check_time()
        if i == len(order):
            return True
        s = order[i]
        for p in (prob.allowed_for(s) & subset):
            if all(asg.get(n) != p for n in adj[s]):
                asg[s] = p
                if bt(i + 1):
                    return True
                del asg[s]
        return False
    return asg.copy() if bt(0) else None


def _assign_backtrack(prob, pal_ids, forb2):
    # The pure-Python fallback is O(2^P . P^n) worst case; bound it by wall clock
    # so an oversized no-OR-Tools run fails with a clear, actionable error
    # instead of hanging (audit A2). A TimeoutError (not ValueError) is raised so
    # the solve_compliant degradation ladder does not silently treat a time-out
    # as IM3 infeasibility.
    deadline = time.monotonic() + SOLVER_TIME_S

    def check_time():
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"colour assignment: no solution within {SOLVER_TIME_S}s using "
                f"the pure-Python backtracking fallback; install OR-Tools "
                f"(CP-SAT) or reduce the problem size")

    def clean(S):
        return not any(K <= S for K in forb2)
    for k in range(1, len(pal_ids) + 1):
        for subset in itertools.combinations(pal_ids, k):
            check_time()
            S = set(subset)
            if not clean(S):
                continue
            if any(not (prob.allowed_for(s) & S) for s in prob.stations):
                continue
            asg = _colourable(prob, S, check_time)
            if asg is not None:
                return asg, set(asg.values())
    raise ValueError("colour assignment infeasible")
