"""Boundary-frequency pinning + slot_demand-driven Problem construction."""
import pytest
import boundary as B
import excel_io
from model import _mk_palette, Problem, KAVACH_F0


def test_boundary_stations_are_first_and_last():
    assert B.boundary_stations([5, 6, 7, 8]) == [5, 8]
    assert B.boundary_stations([9]) == [9]
    assert B.boundary_stations([]) == []


def test_read_boundary_file(tmp_path):
    f = tmp_path / "bnd.csv"
    f.write_text("station_id,pair_id\n10001,3\n10022,6\n")
    pins = B.read_boundary_file(str(f))
    assert pins == {10001: 3, 10022: 6}


def test_read_boundary_file_requires_columns(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="station_id"):
        B.read_boundary_file(str(f))


def test_apply_pins_sets_allowed():
    prob = Problem([1, 2, 3], {1: 4, 2: 4, 3: 4}, {1: 2, 2: 2, 3: 2},
                   _mk_palette(), reuse_window=1, f0=KAVACH_F0)
    B.apply_pins(prob, {1: 3, 3: 5})
    assert prob.allowed_for(1) == {3}
    assert prob.allowed_for(3) == {5}
    assert prob.allowed_for(2) == {p.id for p in prob.palette}   # unpinned = all


def test_apply_pins_rejects_pair_not_in_palette():
    prob = Problem([1], {1: 4}, {1: 2}, _mk_palette(), f0=KAVACH_F0)
    with pytest.raises(ValueError, match="not in palette"):
        B.apply_pins(prob, {1: 999})


def test_resolve_pins_prefers_file(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("station_id,pair_id\n7,2\n")
    pins = B.resolve_pins([7, 8, 9], _mk_palette(), boundary_file=str(f),
                          interactive=False)
    assert pins == {7: 2}


def test_resolve_pins_none_when_noninteractive_and_no_file():
    assert B.resolve_pins([1, 2], _mk_palette(), interactive=False) == {}


def test_build_problem_uses_slot_demand():
    import slot_demand as SD
    chart = {"stations": [10001, 10002],
             "sta_slots": {10001: 99, 10002: 99},     # legacy values (ignored)
             "loco_slots": {10001: 5, 10002: 13},     # peak onboard
             "signals": {10001: 2, 10002: 2},
             "existing_pair": {}}
    prob = excel_io.build_problem(chart, use_slot_demand=True, reuse_window=1)
    # n_station must come from slot_demand, not the legacy 99
    exp = SD.slot_demand(SD.StationDemandInputs(peak_locos=5, last_stop_signals=2))["n_station"]
    assert prob.sta_slots[10001] == exp
    assert prob.sta_slots[10001] != 99


def test_reserved_pairs_picks_highest():
    pal = _mk_palette()                       # ids 1..7
    assert B.reserved_pairs(pal, 2) == {6, 7}
    assert B.reserved_pairs(pal, 0) == set()


def test_reserve_for_boundary_splits_palette():
    prob = Problem([1, 2, 3, 4], {i: 4 for i in range(1, 5)},
                   {i: 2 for i in range(1, 5)}, _mk_palette(), reuse_window=1,
                   f0=KAVACH_F0)
    B.reserve_for_boundary(prob, {6, 7})
    assert prob.allowed_for(1) == {6, 7}                  # boundary (first)
    assert prob.allowed_for(4) == {6, 7}                  # boundary (last)
    assert prob.allowed_for(2) == {1, 2, 3, 4, 5}         # interior: non-reserved
    assert 6 not in prob.allowed_for(2)


def test_reserve_all_pairs_raises():
    prob = Problem([1], {1: 4}, {1: 2}, _mk_palette(), f0=KAVACH_F0)
    with pytest.raises(ValueError, match="interior"):
        B.reserve_for_boundary(prob, {p.id for p in prob.palette})


def test_registry_read_slice_update(tmp_path):
    reg = tmp_path / "boundaries.csv"
    # section A run earlier fixed station 100 (a shared boundary) to pair 6
    reg.write_text("station_id,pair_id\n100,6\n")
    registry = B.read_registry(str(reg))
    # section B contains 100 (its first/boundary) and 101..103
    pins = B.registry_pins_for(registry, [100, 101, 102, 103])
    assert pins == {100: 6}
    # after B solves, write its boundary assignments back
    colour = {100: 6, 101: 1, 102: 2, 103: 4}
    merged = B.update_registry(str(reg), [100, 101, 102, 103], colour)
    assert merged[100] == 6 and merged[103] == 4      # B's boundaries recorded
    assert B.read_registry(str(reg)) == merged        # persisted


def test_registry_missing_file_is_empty(tmp_path):
    assert B.read_registry(str(tmp_path / "nope.csv")) == {}


def test_pinned_station_keeps_its_pair_after_solve():
    pytest.importorskip("ortools")
    import allocation_solver as A
    ids = list(range(1, 9))
    prob = A.Problem(ids, {i: 4 for i in ids}, {i: 2 for i in ids},
                     A._mk_palette(), reuse_window=3, f0=A.KAVACH_F0)
    B.apply_pins(prob, {1: 6})           # pin first station to pair 6
    res = A.solve_compliant(prob)
    assert res["colour"][1] == 6
    assert res["errors"] == []
