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
- **Compliance + justification reporting** and **Excel/CSV I/O**.

Standards referenced: RDSO SPN/196/2020 (multiple-access scheme & TDMA frame),
RDSO TAN clause 4.11 (allocation rules), SPN/196 clause 17.14 (frame-offset).

## Quick start

```bash
cd frequency-timeslot-analysis
pip install -r requirements.txt

# run the built-in worked example (synthetic data):
python3 allocation_solver.py

# or run on your own RDSO frequency-allocation chart:
python3 run_allocation.py  your_chart.xlsx  out.xlsx  --window 4
```

`run_allocation.py` reads an RDSO "Frequency Allocation Chart" workbook, prints
the full per-station allocation + compliance + change justification, and writes
an output workbook (Allocation / Compliance / Justification sheets) plus a CSV.

CLI options: `--window N` (interference radius in stations), `--palette FILE`
(custom palette CSV/XLSX), `--f0 MHZ` (control frequency),
`--slot-strategy {offset0,compact}`, `--gap-ms N`.

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
  im3_analysis.py     standalone IM3 study (illustrative)
  tests/              pytest suite
  optimization-framework.md   the two-layer method (design rationale)
```

## Tests

```bash
cd frequency-timeslot-analysis
pip install pytest
pytest tests/ -q
```

CI runs the suite on Python 3.9 / 3.11, with and without OR-Tools.

## License

MIT — see [LICENSE](LICENSE).
