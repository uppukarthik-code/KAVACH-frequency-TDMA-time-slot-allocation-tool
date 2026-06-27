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
    --palette FILE    frequency palette from a .csv or .xlsx (columns:
                      pair, fS, fM; optional row pair=f0 for the control freq).
                      Default: built-in KAVACH reference palette.
    --f0 MHZ          control/emergency frequency (overrides palette/default)

Examples
    python3 run_allocation.py example_chart.xlsx
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

    # --- read -> solve -> write ---
    gap_slots = None if gap_ms is None else math.ceil(gap_ms / A.MARKER_MS)
    chart = excel_io.read_chart(inp)
    prob = excel_io.build_problem(chart, palette=palette, f0=f0, reuse_window=window)
    # staggering (residual three-tone IM minimisation) is always on in the pipeline
    result = A.solve_compliant(prob, slot_strategy=strategy, gap_slots=gap_slots)
    excel_io.write_compliant_xlsx(out, prob, result, chart["existing_pair"])
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

    print(f"\nWritten:\n  {out}   (Allocation / Compliance / Justification sheets)"
          f"\n  {csv_path}   (per-station table)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
