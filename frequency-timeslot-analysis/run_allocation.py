#!/usr/bin/env python3
"""
KAVACH frequency / time-slot allocation — single entry point.

Reads an RDSO "FREQUENCY ALLOCATION CHART" Excel workbook, generates a
TAN/SPN-compliant allocation, prints the full per-station result, and writes an
output workbook (Allocation / Compliance / Justification sheets) plus a CSV.

USAGE
-----
    python3 run_allocation.py  <input.xlsx>  [output.xlsx]  [options]

Options
    --window N        interference radius in stations (default 4 ~ 10-15 km)
    --rf-range KM     use the real RF-range interference model from the chart's
                      tower latitude/longitude (overrides --window when lat/long
                      are present for every station)
    --palette FILE    frequency palette from a .csv or .xlsx (columns:
                      pair, fS, fM; optional row pair=f0 for the control freq).
                      Default: built-in KAVACH reference palette.
    --f0 MHZ          control/emergency frequency (overrides palette/default)
    --legacy-slots    use the chart's pre-computed slot columns instead of the
                      (default) spec-traceable slot_demand calculator
    --peak-cap N      Railway-Board operational cap on supervised trains per
                      station (RB letter 31.07.2023): e.g. 20 initial, 24 final.
                      Default: uncapped (use the chart's peak figure as-is).
    --boundary FILE   CSV (station_id,pair_id) pinning boundary-station pairs to
                      the adjacent section (scalable; no prompt)
    --no-boundary     do not pin boundary stations (tool chooses freely)
                      [default: if no --boundary and run interactively, ASK]
    --reserve-pairs N reserve the N highest pairs for boundary stations only
                      (interior never uses them -> neighbours can't clash)
    --registry FILE   national boundary registry CSV: pin any station a
                      neighbouring section already fixed, then write this
                      section's boundary pairs back (run sections in order)

Examples
    python3 run_allocation.py SECAB_FreqAllocation.xlsx
    python3 run_allocation.py chart.xlsx out.xlsx --window 4
    python3 run_allocation.py chart.xlsx --palette palette.csv --f0 402.350

Requirements
    pip install openpyxl          # to read/write Excel (required)
    pip install ortools           # optional: enables CP-SAT + wider-separation
                                  #           (a pure-Python fallback runs without it)
Notes
    - Keep this file next to allocation_solver.py / excel_io.py (same folder).
"""
import math
import os
import sys

# make sibling modules importable regardless of the working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    inp = argv[1]
    out = None
    window = 4
    palette_file = None
    f0_cli = None
    strategy = "offset0"
    gap_ms = None
    legacy_slots = False        # default: compute slots with slot_demand
    boundary_file = None        # CSV of station_id,pair_id (scalable boundary pins)
    no_boundary = False         # skip boundary pinning entirely
    reserve_n = 0               # reserve N pairs for boundary stations only
    registry_file = None        # national boundary registry CSV (read + write back)
    peak_cap = None             # Railway-Board operational cap on supervised trains
    rf_range = None             # RF interference radius (km); uses chart lat/long
    rest = argv[2:]
    i = 0
    while i < len(rest):
        if rest[i] == "--window" and i + 1 < len(rest):
            window = int(rest[i + 1]); i += 2
        elif rest[i] == "--palette" and i + 1 < len(rest):
            palette_file = rest[i + 1]; i += 2
        elif rest[i] == "--f0" and i + 1 < len(rest):
            f0_cli = float(rest[i + 1]); i += 2
        elif rest[i] == "--slot-strategy" and i + 1 < len(rest):
            strategy = rest[i + 1]; i += 2
        elif rest[i] == "--gap-ms" and i + 1 < len(rest):
            gap_ms = float(rest[i + 1]); i += 2
        elif rest[i] == "--legacy-slots":
            legacy_slots = True; i += 1
        elif rest[i] == "--boundary" and i + 1 < len(rest):
            boundary_file = rest[i + 1]; i += 2
        elif rest[i] == "--no-boundary":
            no_boundary = True; i += 1
        elif rest[i] == "--reserve-pairs" and i + 1 < len(rest):
            reserve_n = int(rest[i + 1]); i += 2
        elif rest[i] == "--registry" and i + 1 < len(rest):
            registry_file = rest[i + 1]; i += 2
        elif rest[i] == "--peak-cap" and i + 1 < len(rest):
            peak_cap = int(rest[i + 1]); i += 2
        elif rest[i] == "--rf-range" and i + 1 < len(rest):
            rf_range = float(rest[i + 1]); i += 2
        else:
            out = rest[i]; i += 1
    if out is None:
        base = os.path.splitext(os.path.basename(inp))[0]
        out = f"{base}_compliant.xlsx"

    if not os.path.exists(inp):
        print(f"ERROR: input file not found: {inp}")
        return 1
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("ERROR: openpyxl is required to read Excel. Run: pip install openpyxl")
        return 1

    import allocation_solver as A
    import excel_io

    # --- palette + f0 (from file/CLI, else built-in KAVACH reference set) ---
    palette, f0 = None, A.KAVACH_F0
    src = "built-in KAVACH reference palette"
    if palette_file:
        if not os.path.exists(palette_file):
            print(f"ERROR: palette file not found: {palette_file}")
            return 1
        palette, pf0 = excel_io.read_palette(palette_file)
        if pf0 is not None:
            f0 = pf0
        src = f"{palette_file} ({len(palette)} pairs)"
    if f0_cli is not None:
        f0 = f0_cli

    # --- read -> (slot demand) -> boundary pins -> solve -> write ---
    import boundary
    import multipair
    import slot_demand as SD
    gap_slots = None if gap_ms is None else math.ceil(gap_ms / A.MARKER_MS)
    chart = excel_io.read_chart(inp)
    positions = chart.get("positions") if rf_range is not None else None
    if rf_range is not None and positions is None:
        print("Note: --rf-range given but chart has no lat/long; using station "
              f"window (reuse_window={window}) instead")
    prob = excel_io.build_problem(chart, palette=palette, f0=f0,
                                  reuse_window=window,
                                  use_slot_demand=not legacy_slots,
                                  peak_load_cap=peak_cap,
                                  positions=positions, rf_range_km=rf_range)
    if positions is not None:
        print(f"Interference: real RF-range model, radius {rf_range} km "
              f"(from chart lat/long)")

    # spec-traceable slot demand summary + per-section spectrum roll-up (default ON)
    src_slots = "legacy chart column" if legacy_slots else "slot_demand (RDSO-traceable)"
    # per-station slot demand (n_station, n_loco) from the spec-traceable model
    demand = {}
    for sid in chart["stations"]:
        sig = chart.get("signals", {}).get(sid, 2)
        d = SD.slot_demand(SD.StationDemandInputs(
            peak_locos=chart["loco_slots"].get(sid, 0),
            last_stop_signals=sig, peak_load_cap=peak_cap))
        d["last_stop_signals"] = sig
        demand[sid] = d
    roll = SD.section_rollup(
        {sid: SD.StationDemandInputs(peak_locos=chart["loco_slots"].get(sid, 0),
                                     last_stop_signals=chart.get("signals", {}).get(sid, 2),
                                     peak_load_cap=peak_cap)
         for sid in chart["stations"]}, reuse_window=window,
        positions=(chart.get("positions") if rf_range is not None else None),
        rf_range_km=rf_range)
    if peak_cap is not None:
        print(f"Peak-load: supervised trains capped at {peak_cap}/station "
              f"(Railway Board, RB letter 31.07.2023)")
    print(f"Slots  : demand from {src_slots}; section needs >= "
          f"{roll['freq_pairs_min']} frequency pairs "
          f"(total demand {roll['total_slots']} slots; "
          + ("all stations fit one pair)" if roll["feasible_per_station"]
             else f"OVER 44: {roll['stations_exceeding_one_pair']})"))

    # boundary frequency handling -- never assume; sources combine in this order
    pins = {}
    if not no_boundary:
        # (a) reserve a sub-palette for boundary stations only
        if reserve_n > 0:
            res = boundary.reserved_pairs(prob.palette, reserve_n)
            boundary.reserve_for_boundary(prob, res)
            print(f"Boundary: reserved pairs {sorted(res)} for boundary stations")
        # (b) national registry: pin any station a neighbouring section already fixed
        if registry_file:
            pins.update(boundary.registry_pins_for(
                boundary.read_registry(registry_file), prob.stations))
        # explicit boundary file, else ASK if interactive (suppressed when a
        # registry is supplying the pins)
        pins.update(boundary.resolve_pins(
            prob.stations, prob.palette, boundary_file=boundary_file,
            interactive=(False if registry_file else None)))
        if pins:
            boundary.apply_pins(prob, pins)
            print(f"Boundary: pinned {pins}")

    # staggering (always on) + automatic multi-pair split for over-capacity
    # stations (demand > one pair's 44 markers). `eprob` has any split sub-units.
    result, eprob = multipair.solve_multipair(prob, slot_strategy=strategy,
                                              gap_slots=gap_slots)
    multi = {o: ps for o, ps in result["station_pairs"].items() if len(ps) > 1}
    if multi:
        print(f"Multi-pair: stations over {multipair.usable_markers(prob)} markers "
              f"split across pairs -> {multi}")

    # (b) write this section's boundary assignments back (original ids -> pair)
    if registry_file and not no_boundary:
        colour_by_orig = {o: ps[0] for o, ps in result["station_pairs"].items()}
        boundary.update_registry(registry_file, prob.stations, colour_by_orig)
        print(f"Boundary: registry updated -> {registry_file}")

    prob = eprob   # report on the (possibly expanded) problem -> sub-units shown
    # provenance: tie this output back to the code version + exact input
    import datetime
    import provenance as PROV
    prov = PROV.build(
        inp, reuse_window=window, slot_source=src_slots,
        spectrum=result["spectrum"],
        validation=("PASS" if not result["errors"] else f"{result['errors']}"),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"))
    if peak_cap is not None:
        prov.append(("peak_load_cap", peak_cap))
    excel_io.write_compliant_xlsx(out, prob, result, chart["existing_pair"],
                                  provenance=prov)
    csv_path = os.path.splitext(out)[0] + ".csv"
    A.write_allocation_csv(csv_path, prob, result)

    # --- print result ---
    print(f"\nInput  : {inp}   ({len(chart['stations'])} stations, "
          f"reuse_window={window})")
    print(f"Palette: {src} | f0 = {f0} MHz")
    imco = result.get('im_coincidence')
    stg = ("staggered" if result.get('staggered') else "offset-0 (no CP-SAT; "
           "staggering unavailable)")
    co = f", residual 3-tone coincidences = {imco[0]}" if imco else ""
    print(f"Slots  : {stg}, frame-offset-0 gap = "
          f"{result['gap_slots'] * A.MARKER_MS:.0f} ms (SPN/196 17.14){co}")
    print(f"Solver: CP-SAT={A._HAS_CPSAT} | IM3: {result['im3_note']}")
    print(f"Spectrum used: {result['spectrum']} pairs {sorted(result['used_pairs'])}")
    print(f"Validation: "
          f"{'PASS - zero interference' if not result['errors'] else result['errors']}\n")

    if not legacy_slots:
        print("STATION & LOCO SLOT DEMAND (spec-traceable; SPN/196 Annexure-C)")
        A.slot_demand_table(chart["stations"], demand)
        print()

    print("FREQUENCY & TIME-SLOT ALLOCATION AT EVERY STATION")
    A.print_allocation_table(prob, result)

    print("\nCOMPLIANCE (RDSO TAN 4.11 + SPN/196)")
    for clause, status in A.compliance_report(prob, result):
        print(f"  [{status.split()[0]:4}] {clause}: {status}")

    changes = [r for r in A.justify_changes(prob, result, chart["existing_pair"])
               if r[3] != "unchanged"]
    print(f"\nCHANGES vs existing 'Proposed Frequency Pair' ({len(changes)} changed)")
    for s, old, new, reason in changes:
        print(f"  station {s}: pair {old} -> {new}  ({reason})")

    print(f"\nWritten:\n  {out}   (Allocation / Compliance / Justification / "
          f"Provenance sheets)\n  {csv_path}   (per-station table)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
