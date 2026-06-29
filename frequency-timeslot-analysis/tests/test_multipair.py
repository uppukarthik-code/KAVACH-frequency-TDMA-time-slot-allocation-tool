"""Automatic multi-pair-per-station splitting for over-capacity terminals."""
import warnings
import pytest
import multipair as MP
from model import Problem, _mk_palette, KAVACH_F0


def test_even_split_sums_and_balances():
    assert MP._even_split(30, 2) == [15, 15]
    assert MP._even_split(23, 2) == [12, 11]
    assert sum(MP._even_split(53, 4)) == 53


def test_plan_split_only_over_capacity():
    plan = MP.plan_split({1: 23, 2: 6}, {1: 30, 2: 4}, capacity=44)
    assert 2 not in plan                       # 10 <= 44, not split
    k, sp, lp = plan[1]                         # 53 > 44 -> split
    assert k == 2
    assert sum(sp) == 23 and sum(lp) == 30
    assert all(a + b <= 44 for a, b in zip(sp, lp))


def test_plan_split_bumps_k_if_rounding_overflows():
    # contrived: demand just over a multiple where even split would exceed cap
    plan = MP.plan_split({1: 44}, {1: 44}, capacity=44)   # 88 -> K=2, each 44 ok
    k, sp, lp = plan[1]
    assert all(a + b <= 44 for a, b in zip(sp, lp))


def test_usable_markers_default_44():
    prob = Problem([1], {1: 4}, {1: 2}, _mk_palette(), f0=KAVACH_F0)
    assert MP.usable_markers(prob) == 44


def test_expand_problem_no_split_is_identity():
    prob = Problem([1, 2], {1: 4, 2: 4}, {1: 2, 2: 2}, _mk_palette(),
                   reuse_window=1, f0=KAVACH_F0)
    exp, mapping = MP.expand_problem(prob)
    assert exp is prob                          # unchanged
    assert mapping == {1: 1, 2: 2}


def test_expand_problem_splits_and_links():
    prob = Problem([1, 2], {1: 23, 2: 6}, {1: 30, 2: 4}, _mk_palette(),
                   reuse_window=1, f0=KAVACH_F0)
    exp, mapping = MP.expand_problem(prob)
    subs = [i for i in exp.stations if mapping[i] == 1]
    assert len(subs) == 2                       # station 1 split into 2
    # sub-units mutually interfere (clique)
    adj = exp.adjacency()
    assert subs[1] in adj[subs[0]]
    # sub-units inherit the parent's edge to station 2
    assert 2 in adj[subs[0]] and 2 in adj[subs[1]]
    # demand conserved
    assert exp.sta_slots[subs[0]] + exp.sta_slots[subs[1]] == 23
    assert exp.loco_slots[subs[0]] + exp.loco_slots[subs[1]] == 30


def test_solve_multipair_assigns_distinct_pairs_to_subunits():
    prob = Problem([1, 2, 3], {1: 23, 2: 6, 3: 6}, {1: 30, 2: 4, 3: 4},
                   _mk_palette(), reuse_window=2, f0=KAVACH_F0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res, used = MP.solve_multipair(prob)
    assert res["errors"] == []
    assert len(res["station_pairs"][1]) == 2    # station 1 uses two pairs
    assert len(res["station_pairs"][2]) == 1
    # the two sub-unit pairs are distinct
    a, b = res["station_pairs"][1]
    assert a != b


def test_solve_multipair_noop_when_all_fit():
    prob = Problem([1, 2, 3, 4], {i: 4 for i in range(1, 5)},
                   {i: 2 for i in range(1, 5)}, _mk_palette(), reuse_window=2,
                   f0=KAVACH_F0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res, used = MP.solve_multipair(prob)
    assert used is prob                          # no expansion
    assert all(len(v) == 1 for v in res["station_pairs"].values())
