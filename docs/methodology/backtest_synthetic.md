# Back-test — method and results (synthetic chart)

This document records how the slot-demand + allocation model is validated
end-to-end, and the results on a **synthetic** chart that is committed to the
repository so anyone can reproduce every published number.

> **Data policy.** The repository never contains real operational data. The
> synthetic chart uses invented Stationary KAVACH ids (`10001–10022`) and
> invented onboard counts, engineered only to *match the structure* of a real
> single section. The real-section validation was run **locally** against an
> approved RDSO chart and is summarised in §4; its inputs and outputs are
> deliberately not stored here.

---

## 1. What is being validated

The full pipeline, exactly as the CLI runs it:

```
read chart ─▶ slot_demand (per station) ─▶ section roll-up (min spectrum)
           ─▶ allocate (colour + slot schedule + IM3) ─▶ multi-pair split
```

Two classes of output are checked separately:

| Layer | What | Stability |
|---|---|---|
| **Slot-demand arithmetic** | `n_station`, `n_loco`, totals, min pairs | Pure byte math — **solver-independent**, frozen exactly |
| **Allocation invariants** | zero interference, spectrum ≥ lower bound, over-capacity split | Asserted as properties, **not** by pinning solver-chosen pair ids |

This split is deliberate: the arithmetic must match to the integer in every CI
cell, while the allocation is checked by its *guarantees* so the test survives
OR-Tools version changes and the pure-Python fallback.

---

## 2. The reproducible artefacts (all committed)

| File | Role |
|---|---|
| `frequency-timeslot-analysis/tests/fixtures/make_synthetic_chart.py` | Deterministic generator for the chart (no randomness; byte-reproducible) |
| `…/fixtures/synthetic_chart.xlsx` | The synthetic chart workbook |
| `…/fixtures/make_golden.py` | Re-freezes the golden values from the fixture |
| `…/fixtures/synthetic_chart_expected.json` | Frozen, solver-independent golden numbers |
| `…/tests/test_backtest_golden.py` | Runs the pipeline and asserts against the golden file |

Reproduce from scratch:

```bash
cd frequency-timeslot-analysis
python3 tests/fixtures/make_synthetic_chart.py   # rebuild the workbook
python3 tests/fixtures/make_golden.py            # re-freeze golden values
python3 -m pytest tests/test_backtest_golden.py -q
```

CI runs the golden test on every push across **py3.9/3.11 × {with, without}
OR-Tools**, so drift is caught automatically.

---

## 3. Results on the synthetic chart

22 synthetic stations, reuse window = 4.

| Metric | Value |
|---|---|
| Stations | 22 |
| Total Stationary-Tx slots | 192 |
| Total Loco-Tx slots | 159 |
| Total slot demand | 351 |
| **Minimum frequency pairs (interference lower bound)** | **5** |
| Stations exceeding one pair (44 markers) | `10011` (53 slots) |
| Allocation result | **zero interference (PASS)** |
| Over-capacity handling | `10011` split across 2 distinct pairs |

Qualitatively this reproduces the real section's headline findings — **most
stations fit one pair, the interference clique forces five pairs, and a busy
junction terminal must be split across pairs** — on numbers that are safe to
publish.

---

## 4. Real-section back-test (run locally, not stored)

For the record, the same pipeline was run locally against an **approved RDSO
frequency-allocation chart for a real section**. Summary of that run (inputs/outputs kept
off the repository per the data policy):

* The slot-demand model reproduced the approved per-station Stationary-Tx slot
  counts for the large majority of stations, and **never exceeded** the approved
  figure (i.e. never under-provisioned, never proposed an unsafe reduction).
* The section roll-up predicted the **same spectrum** (number of frequency
  pairs) that the approved chart uses, without running the allocator.
* The allocator produced a zero-interference assignment consistent with the
  deployed pattern.

Anyone with authorised access to the approved chart can repeat this locally by
pointing the CLI at that workbook:

```bash
python3 run_allocation.py <approved_chart>.xlsx --window 4
```

The synthetic golden test in §2 is the **public, reproducible** stand-in for
this private validation.

---

## 5. For the IRSE article

This back-test supplies the article's *Methods* and *Validation* sections:

* **Methods** → [`slot_demand_derivation.md`](slot_demand_derivation.md) — the
  formula with every constant traced to SPN/196 Annexure-C.
* **Validation** → this document — the synthetic reproduction (publishable) plus
  the summary of the private real-section match.
* **Reproducibility statement** → "All reported figures are regenerated in CI
  from a committed synthetic fixture; see `tests/test_backtest_golden.py`."
