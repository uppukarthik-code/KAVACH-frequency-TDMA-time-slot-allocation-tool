"""Tests for the spec-traceable slot-demand calculator."""
import math
import pytest
import slot_demand as SD


def test_traceability_lists_every_constant():
    rows = SD.traceability()
    assert len(rows) >= 8
    # each row cites a source
    assert all(src for _, _, _, src in rows)


def test_mandatory_floor_never_under_provisions():
    """n_station must cover at least the mandatory header+MA airtime for N locos
    (one packet per loco per cycle, spec C.4.2)."""
    for N in range(0, 30):
        ns = SD.station_tx_slots(N, profiled_locos=0, broadcast_b=0)
        assert ns >= math.ceil(N * SD.B_REG / SD.USABLE_B)


def test_station_slots_monotonic_in_locos():
    prev = -1
    for N in range(0, 25):
        ns = SD.station_tx_slots(N, profiled_locos=2)
        assert ns >= prev
        prev = ns


def test_profiled_capped_at_peak():
    # asking for more profiled locos than exist is clamped to N
    a = SD.slot_demand(SD.StationDemandInputs(peak_locos=3, profiled_locos=10))
    b = SD.slot_demand(SD.StationDemandInputs(peak_locos=3, profiled_locos=3))
    assert a["n_station"] == b["n_station"]


def test_loco_slots_one_per_leading_loco():
    # a 230-bit uplink packet fits one marker -> one slot per leading loco
    assert SD.loco_tx_slots(0) == 0
    assert SD.loco_tx_slots(7) == 7


def test_leading_defaults_to_peak():
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=9))
    assert r["n_loco"] == 9


def test_estimate_peak_locos_from_layout():
    # 3 berthing + 2 directions * ceil(10/3)=4 each = 3 + 8 = 11
    n = SD.estimate_peak_locos(berthing_tracks=3, directions=2,
                               coverage_km=10.0, headway_km=3.0)
    assert n == 3 + 2 * math.ceil(10 / 3)


def test_capacity_and_pairs():
    # within one pair
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=10, last_stop_signals=2))
    assert r["total_slots"] == r["n_station"] + r["n_loco"]
    assert r["fits_one_pair"] == (r["total_slots"] <= SD.WORKING_MARKERS)
    # force over one pair
    big = SD.slot_demand(SD.StationDemandInputs(peak_locos=40, leading_locos=40))
    assert big["freq_pairs_min"] >= 2


def test_known_worked_value():
    # N=10, 2 profiled: dl = 10*35 + 2*85 + 16 = 536 B; /54 -> ceil(9.9)=10
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=10, profiled_locos=2))
    expected = math.ceil((10 * SD.B_REG + 2 * SD.DL_PROFILE_B + SD.DL_BROADCAST_B)
                         / SD.USABLE_B)
    assert r["n_station"] == expected


def test_to_problem_slots_shapes():
    stations = {101: SD.StationDemandInputs(peak_locos=5),
                102: SD.StationDemandInputs(peak_locos=8)}
    sta, loco = SD.to_problem_slots(stations)
    assert set(sta) == {101, 102} and set(loco) == {101, 102}
    assert all(isinstance(v, int) for v in sta.values())


def test_max_interfering_group_window():
    ids = list(range(12))
    assert SD._max_interfering_group(ids, reuse_window=1) == 2
    assert SD._max_interfering_group(ids, reuse_window=4) == 5
    assert SD._max_interfering_group([0, 1], reuse_window=4) == 2   # capped at n


def test_max_interfering_group_positions():
    # five stations 3 km apart, 10 km range -> any 10 km window holds up to 4
    pos = {i: 3.0 * i for i in range(5)}
    assert SD._max_interfering_group(list(pos), positions=pos, rf_range_km=10.0) == 4


def test_section_rollup_window_model():
    # window=4 over many stations -> 5 frequency pairs minimum (path-power clique)
    stations = {i: SD.StationDemandInputs(peak_locos=6, last_stop_signals=2)
                for i in range(10)}
    r = SD.section_rollup(stations, reuse_window=4)
    assert r["n_stations"] == 10
    assert r["freq_pairs_min"] == 5
    assert r["feasible_per_station"] is True
    assert r["total_slots"] == r["total_station_slots"] + r["total_loco_slots"]


def test_section_rollup_flags_overflow():
    # a station demanding > 44 markers must be flagged infeasible-on-one-pair
    stations = {1: SD.StationDemandInputs(peak_locos=30, leading_locos=30,
                                          profiled_locos=10)}
    r = SD.section_rollup(stations, reuse_window=1)
    assert 1 in r["stations_exceeding_one_pair"]
    assert r["feasible_per_station"] is False


def test_zero_locos():
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=0, last_stop_signals=0))
    assert r["n_station"] == 0 and r["n_loco"] == 0


def test_peak_load_cap_clamps_and_flags():
    # 30 trains requested but final-phase cap is 24 -> demand computed on 24
    capped = SD.slot_demand(SD.StationDemandInputs(
        peak_locos=30, peak_load_cap=SD.PEAK_LOAD_CAP_FINAL))
    at24 = SD.slot_demand(SD.StationDemandInputs(peak_locos=24))
    assert capped["peak_locos"] == 30            # the requested figure is preserved
    assert capped["supervised_locos"] == 24      # but demand uses the cap
    assert capped["peak_load_capped"] is True
    assert capped["n_station"] == at24["n_station"]
    assert capped["n_loco"] == at24["n_loco"]


def test_peak_load_cap_no_effect_below_cap():
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=10, peak_load_cap=24))
    assert r["peak_load_capped"] is False
    assert r["supervised_locos"] == 10


def test_final_cap_fits_one_pair():
    # the policy choice (24 trains) is consistent with the 44-marker pair cap
    r = SD.slot_demand(SD.StationDemandInputs(peak_locos=SD.PEAK_LOAD_CAP_FINAL,
                                              last_stop_signals=2))
    assert r["fits_one_pair"] is True
    assert r["total_slots"] <= SD.WORKING_MARKERS


def test_traceability_cites_peak_load_caps():
    rows = SD.traceability()
    sources = " ".join(str(src) for *_, src in rows)
    assert "spec V4.0" in sources            # spec full-duplex capability cited
    assert "operator policy" in sources      # phased-rollout caps cited
