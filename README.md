# KAVACH Frequency & TDMA Time-Slot Allocation

An open-source optimization tool that generates **RDSO TAN/SPN-compliant
frequency-pair and TDMA time-slot allocations** for KAVACH (Indian Railways
Automatic Train Protection) stations along a railway section minimising radio
spectrum and third-order intermodulation (IM3) interference, and producing an
auditable compliance and change-justification record.

It is a **decision-support tool**: the output is a *proposal* for COE/IRISET
review and Independent Safety Assessment (ISA), not a safety authority.

> ⚠️ **All data in this repository is synthetic / illustrative** — the example
> frequencies, station IDs and section are made-up values for demonstration only.
> They do **not** represent any real deployment. Supply your own chart to use the
> tool.

## What it does

- **Frequency assignment** — list multi-colouring of the station interference
  graph (adjacent stations differ), spectrum-minimal, then maximise the minimum
  adjacent-station frequency separation (RDSO TAN clause 4.11).
- **Intermodulation (IM3) avoidance** — frequency-domain avoidance of 2- and
  3-tone products, plus a Sidon/B₂ palette designer that makes IM3 structurally
  impossible.
- **Time-slot scheduling** — contiguous Stationary-Tx window + non-adjacent Loco
  slots, with SPN/196 17.14 frame-offset-0 (loco data ≥150–200 ms before the
  station window for a fresh same-cycle Movement Authority).
- **Time-domain staggering** — minimise residual 3-tone IM slot-coincidences so
  the products cannot form (requires OR-Tools CP-SAT).
- **Slot-demand calculator** — derives each station's Stationary-Tx / Loco-Tx
  slot counts from first principles, every constant traced to RDSO SPN/196
  Annexure-C (`slot_demand.py`; see `docs/methodology/`).
- **Multi-pair terminals** — a station whose demand exceeds one pair's 44 markers
  is automatically split across multiple frequency pairs (`multipair.py`).
- **Boundary consistency** — pin or reserve frequency pairs at section joins so
  adjacent sections stay consistent when a network is allocated piece-by-piece
  (`boundary.py`).
- **Compliance + justification reporting**, **run provenance** (tool version +
  input hash stamped into each output), and **Excel/CSV I/O**.

Standards referenced: RDSO SPN/196/2020 (multiple-access scheme & TDMA frame),
RDSO TAN clause 4.11 (allocation rules), SPN/196 clause 17.14 (frame-offset).

## Quick start

**One click.** On Windows, double-click **`run.bat`** (or drag your filled chart
onto it); on macOS/Linux run **`./run.sh`**. It installs the dependencies, runs
the allocation on the bundled `KAVACH_input_template.xlsx` (or your chart),
prints the station/loco slots, and writes the output workbook + CSV into
`output/`.

**Or from the command line:**

```bash
cd frequency-timeslot-analysis
pip install -r requirements.txt

# run the built-in worked example (synthetic data):
python3 allocation_solver.py

# or run on your own RDSO frequency-allocation chart:
python3 run_allocation.py  your_chart.xlsx  out.xlsx  --window 4
```

To fill in your own section, copy `KAVACH_input_template.xlsx`, replace the
highlighted cells (one column per station, in geographic order), and run it.

`run_allocation.py` reads an RDSO "Frequency Allocation Chart" workbook, computes
each station's slot demand from first principles, prints the full per-station
allocation + compliance + change justification, and writes an output workbook
(Allocation / Compliance / Justification / **Provenance** sheets) plus a CSV.

CLI options: `--window N` (interference radius in stations), `--palette FILE`
(custom palette CSV/XLSX), `--f0 MHZ` (control frequency),
`--slot-strategy {offset0,compact}`, `--gap-ms N`, `--legacy-slots` (use the
chart's pre-computed slot columns), `--peak-cap N` (cap supervised trains per
station), and `--boundary FILE` / `--reserve-pairs N` / `--registry FILE` for
section-boundary consistency.

## Solver backends

- **OR-Tools CP-SAT** (recommended) — enables the wider-separation objective and
  the time-domain IM staggering. See `frequency-timeslot-analysis/requirements.txt`
  for version/setup notes.
- **Pure-Python fallback** — an exact backtracking colourer runs automatically if
  OR-Tools is unavailable (staggering is skipped; residual 3-tone IM is reported
  for the ISA).

## Project layout

```
frequency-timeslot-analysis/
  model.py            data model, IM3 engine, Sidon design, shared CP-SAT infra
  colour.py           frequency-pair (colour) assignment
  slots.py            TDMA slot scheduling
  report.py           allocation table + compliance + justification
  allocation_solver.py pipeline (solve / solve_compliant / validate) + facade
  stagger.py          time-domain IM minimisation
  dense_area.py       directional-antenna + joint freq×slot extensions
  excel_io.py         RDSO chart read / output workbook write
  run_allocation.py   command-line entry point
  slot_demand.py      spec-traceable slot-demand calculator (SPN/196 Annexure-C)
  multipair.py        auto-split busy terminals across >1 frequency pair
  boundary.py         boundary-frequency pinning + national registry
  provenance.py       stamps each output with tool version + input hash
  im3_analysis.py     standalone IM3 study (illustrative)
  tests/              pytest suite (+ fixtures/ golden back-test)
  optimization-framework.md   the two-layer method (design rationale)
docs/methodology/
  slot_demand_derivation.md   the slot-demand formula, every constant traced
  backtest_synthetic.md       back-test method + results (synthetic chart)
```

## Reproducibility & audit

Every published figure is regenerated from committed sources:

```bash
cd frequency-timeslot-analysis
python3 tests/fixtures/make_synthetic_chart.py   # rebuild the synthetic chart
python3 tests/fixtures/make_golden.py            # re-freeze the golden values
python3 -m pytest tests/test_backtest_golden.py -q
python3 slot_demand.py                           # print the constant-traceability table
```

`docs/methodology/` derives the slot-demand model with every constant traced to
RDSO SPN/196 Annexure-C, and documents the back-test on a synthetic chart. All
data in this repository is synthetic/illustrative.

## Tests

```bash
cd frequency-timeslot-analysis
pip install pytest
pytest tests/ -q
```

CI runs the suite on Python 3.9 / 3.11, with and without OR-Tools.

## License

MIT — see [LICENSE](LICENSE).
