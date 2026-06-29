#!/usr/bin/env python3
"""
Freeze the SOLVER-INDEPENDENT slot-demand calculations for the synthetic chart
into `synthetic_chart_expected.json` (the golden file).

What is frozen here is pure arithmetic from the RDSO-traceable slot_demand model
(packet bytes -> markers); it does not depend on OR-Tools, the random seed, or
the ortools version, so it is stable across every CI matrix cell. The golden
test (test_backtest_golden.py) re-derives these numbers and asserts they match,
then separately checks the *allocation invariants* (feasibility, the over-capacity
split) without pinning solver-chosen pair indices.

Run:  python3 make_golden.py     # regenerates the JSON from the committed fixture
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # package root
FIXTURE = os.path.join(HERE, "synthetic_chart.xlsx")
GOLDEN = os.path.join(HERE, "synthetic_chart_expected.json")
WINDOW = 4


def compute():
    import excel_io
    import slot_demand as SD

    chart = excel_io.read_chart(FIXTURE)
    inputs = {sid: SD.StationDemandInputs(
                  peak_locos=chart["loco_slots"][sid],
                  last_stop_signals=chart["signals"][sid])
              for sid in chart["stations"]}
    per_station = {}
    for sid in chart["stations"]:
        r = SD.slot_demand(inputs[sid])
        per_station[str(sid)] = {"peak_locos": chart["loco_slots"][sid],
                                 "last_stop_signals": chart["signals"][sid],
                                 "n_station": r["n_station"],
                                 "n_loco": r["n_loco"],
                                 "total_slots": r["total_slots"],
                                 "fits_one_pair": r["fits_one_pair"]}
    roll = SD.section_rollup(inputs, reuse_window=WINDOW)
    return {
        "_about": "Golden values for the synthetic back-test chart. Pure "
                  "slot_demand arithmetic (RDSO/SPN-196 traceable); "
                  "solver-independent. Regenerate with make_golden.py.",
        "reuse_window": WINDOW,
        "n_stations": len(chart["stations"]),
        "per_station": per_station,
        "section": {
            "freq_pairs_min": roll["freq_pairs_min"],
            "total_slots": roll["total_slots"],
            "total_station_slots": roll["total_station_slots"],
            "total_loco_slots": roll["total_loco_slots"],
            "stations_exceeding_one_pair":
                [str(s) for s in roll["stations_exceeding_one_pair"]],
            "feasible_per_station": roll["feasible_per_station"],
        },
    }


def main():
    data = compute()
    with open(GOLDEN, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {GOLDEN}")
    print(f"  {data['n_stations']} stations, "
          f"{data['section']['freq_pairs_min']} pairs min, "
          f"over-capacity: {data['section']['stations_exceeding_one_pair']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
