"""
Golden end-to-end back-test on the SYNTHETIC chart.

This is the reproducibility / audit anchor: it runs the whole pipeline
(read chart -> slot_demand -> section roll-up -> allocate -> multi-pair split)
on a committed, synthetic fixture and asserts the published numbers.

Two layers, deliberately separated:

  1. Slot-demand arithmetic  -- frozen in synthetic_chart_expected.json. Pure
     RDSO-traceable byte math; solver-independent, so it must match EXACTLY in
     every CI cell (py3.9/3.11 x with/without OR-Tools).

  2. Allocation invariants   -- feasibility (zero interference), the spectrum
     lower bound, and the over-capacity split. Asserted as properties, NOT by
     pinning solver-chosen pair indices, so the test is robust to OR-Tools
     version and to the pure-Python fallback.

Regenerate the fixture + golden file with:
    python3 tests/fixtures/make_synthetic_chart.py
    python3 tests/fixtures/make_golden.py
"""
import json
import os
import warnings

import pytest

import excel_io
import slot_demand as SD
import multipair

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "synthetic_chart.xlsx")
GOLDEN = os.path.join(HERE, "fixtures", "synthetic_chart_expected.json")

pytest.importorskip("openpyxl")


@pytest.fixture(scope="module")
def golden():
    with open(GOLDEN) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def chart():
    return excel_io.read_chart(FIXTURE)


# --- layer 1: solver-independent slot-demand arithmetic -------------------

def test_per_station_slot_demand_matches_golden(chart, golden):
    """Every station's n_station / n_loco / total reproduce the frozen values."""
    assert [str(s) for s in chart["stations"]] == sorted(golden["per_station"])
    for sid in chart["stations"]:
        exp = golden["per_station"][str(sid)]
        r = SD.slot_demand(SD.StationDemandInputs(
                peak_locos=chart["loco_slots"][sid],
                last_stop_signals=chart["signals"][sid]))
        assert r["n_station"] == exp["n_station"], f"n_station drift at {sid}"
        assert r["n_loco"] == exp["n_loco"], f"n_loco drift at {sid}"
        assert r["total_slots"] == exp["total_slots"], f"total drift at {sid}"
        assert r["fits_one_pair"] == exp["fits_one_pair"]


def test_section_rollup_matches_golden(chart, golden):
    inputs = {sid: SD.StationDemandInputs(
                  peak_locos=chart["loco_slots"][sid],
                  last_stop_signals=chart["signals"][sid])
              for sid in chart["stations"]}
    roll = SD.section_rollup(inputs, reuse_window=golden["reuse_window"])
    sec = golden["section"]
    assert roll["freq_pairs_min"] == sec["freq_pairs_min"]
    assert roll["total_slots"] == sec["total_slots"]
    assert roll["total_station_slots"] == sec["total_station_slots"]
    assert roll["total_loco_slots"] == sec["total_loco_slots"]
    assert [str(s) for s in roll["stations_exceeding_one_pair"]] == \
        sec["stations_exceeding_one_pair"]
    assert roll["feasible_per_station"] == sec["feasible_per_station"]


# --- layer 2: allocation invariants (solver-path-independent) -------------

def test_full_allocation_is_feasible_and_splits_over_capacity(chart, golden):
    prob = excel_io.build_problem(chart, reuse_window=golden["reuse_window"],
                                  use_slot_demand=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # staggering-fallback notices
        result, eprob = multipair.solve_multipair(prob)

    # (a) zero interference -- the allocation is valid
    assert result["errors"] == []

    # (b) spectrum is at least the interference lower bound
    assert result["spectrum"] >= golden["section"]["freq_pairs_min"]

    # (c) every over-capacity station is split across >= 2 distinct pairs
    for sid in golden["section"]["stations_exceeding_one_pair"]:
        pairs = result["station_pairs"][int(sid)]
        assert len(pairs) >= 2, f"{sid} should be multi-pair"
        assert len(set(pairs)) == len(pairs), f"{sid} sub-units must differ"

    # (d) every in-capacity station uses exactly one pair
    over = set(golden["section"]["stations_exceeding_one_pair"])
    for sid in chart["stations"]:
        if str(sid) not in over:
            assert len(result["station_pairs"][sid]) == 1


def test_fixture_is_synthetic_not_operational(chart):
    """Guard rail: the committed fixture must use synthetic ids (10001-10022),
    never real deployed Stationary KAVACH ids."""
    assert chart["stations"] == list(range(10001, 10023))
