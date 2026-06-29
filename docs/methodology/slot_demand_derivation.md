# Slot-demand model — formal derivation

This document derives, from first principles, the number of **Stationary KAVACH
transmit slots** (`n_station`) and **Loco KAVACH transmit slots** (`n_loco`) a
station needs, as a function of the number of onboard units it serves and its
track-side data. Every constant is traced to its RDSO source so a reviewer can
audit each term. The model is implemented in
[`frequency-timeslot-analysis/slot_demand.py`](../../frequency-timeslot-analysis/slot_demand.py)
and the constants below are emitted at runtime by `slot_demand.traceability()`.

> All numeric examples here use the **synthetic** back-test chart, not any real
> operational data. See [`backtest_synthetic.md`](backtest_synthetic.md).

---

## 1. The TDMA frame

KAVACH UHF radio runs a fixed 2000 ms TDMA frame divided into **70 markers**
(P1–P70), each 27.5 ms, carrying a 432-bit (54-byte) payload.

| Quantity | Symbol | Value | Source |
|---|---|---|---|
| Working markers (M1..M44 = P2..P45) | `WORKING_MARKERS` | **44** | SPN/196 Annexure-C C.3.2.1 |
| Reserved markers (P1, P46) | — | 2 | C.3.2.9 |
| Marker payload (432 bits) | `USABLE_B` | **54 B** | C.3.2 |

A single frequency **pair** therefore offers **44 usable transmit slots** per
2 s frame. This is the hard ceiling behind the multi-pair split: a station whose
demand exceeds 44 slots cannot be served by one pair.

---

## 2. Downlink (Stationary → Loco) — `n_station`

Each frame, the Stationary KAVACH must transmit, **per onboard unit it serves**:

1. a packet **header** (16 B) — C.3.2.13 header table, and
2. a **Movement Authority (MA)** sub-packet (19 B) — sent *every cycle* per
   loco (MA pp.15–21, C.4.2).

These two are mandatory for every loco every cycle:

```
B_REG = header + MA = 16 + 19 = 35 bytes / loco / frame        [C.4.2]
```

On top of the per-loco MA, **track-profile** data (speed profile, gradient,
level-crossings, turnouts, track condition, TSRs, tag-linking) is broadcast to
the locos that still need it (the *profiled* subset), plus a small fixed
**broadcast/common** overhead (16 B, header table p.13).

The profile burst is built **bottom-up** from the individual sub-packet fields
rather than assumed, so the budget tracks the spec rather than a guessed
constant. With the default field counts
`{ssp:4, grad:4, lc:1, to:2, tc:1, tsr:0, tli:10}` the burst sums to
**81 B** (each sub-packet ≤ 128 B, C-series profile pages 22–31).

### Formula

For a station serving `N` concurrent onboard units, of which `P ≤ N` still need
the profile burst (`profile_burst` bytes each) and a fixed `broadcast` overhead:

```
              ⌈  N · B_REG  +  P · profile_burst  +  broadcast  ⌉
n_station  =  ⌈ ─────────────────────────────────────────────── ⌉
              ⌈                    USABLE_B                       ⌉
```

i.e. total downlink bytes per frame ÷ 54 B per marker, rounded up. The ceiling
is the number of markers (slots) the station must occupy on its pair.

| Term | Meaning | Value |
|---|---|---|
| `N` | peak concurrent onboard units | from chart |
| `B_REG` | header + MA, mandatory per loco | 35 B |
| `P` | profiled subset (≤ N) | ≤ N |
| `profile_burst` | bottom-up profile bytes | 81 B (default) |
| `broadcast` | common overhead | 16 B |
| `USABLE_B` | marker payload | 54 B |

**Worked example (synthetic station 10009, N = 9, P defaults clamped to N):**
`n_station = ⌈(9·35 + … + 16)/54⌉ = 10 slots` (matches the golden fixture).

### Mandatory floor (safety property)

Because the MA is mandatory every cycle, `n_station` can never drop below the
airtime needed just for `N` headers+MAs:

```
n_station  ≥  ⌈ N · B_REG / USABLE_B ⌉
```

This invariant is asserted for `N = 0..29` in
`tests/test_slot_demand.py::test_mandatory_floor_never_under_provisions` — the
model is provably never *under*-provisioned.

---

## 3. Uplink (Loco → Stationary) — `n_loco`

Each leading onboard unit sends one **regular uplink packet** per frame
(29 B = 230 bits, total row p.38). A 29 B packet fits inside one 54 B marker, so
each leading loco needs exactly one Loco-Tx slot:

```
n_loco = N_leading            (defaults to the peak onboard count N)
```

(`loco_tx_slots(0) = 0`, `loco_tx_slots(7) = 7`; see
`test_loco_slots_one_per_leading_loco`.)

---

## 4. Per-station total and the one-pair feasibility test

```
total_slots = n_station + n_loco
fits_one_pair = (total_slots ≤ WORKING_MARKERS = 44)
```

A station with `total_slots > 44` cannot be served by a single pair and is
**automatically split** across `⌈total_slots / 44⌉` pairs (see
[`multipair.py`](../../frequency-timeslot-analysis/multipair.py)).

**Worked example (synthetic junction 10011):** `N = 28` ⇒ `n_station = 25`,
`n_loco = 28`, `total = 53 > 44` ⇒ flagged over-capacity, split across 2 pairs.

---

## 4a. Operational peak-load caps

`N` (supervised trains per Stationary KAVACH) is bounded not only by airtime but
by **operator policy** during phased commissioning. The KAVACH spec V4.0 full-
duplex capability is **44 onboard units**; an operator may cap the number of
supervised trains lower in early phases (enforced in the field via Exit Tags).
The model exposes illustrative example caps:

| Cap | Example value | Basis |
|---|---|---|
| Initial phase (higher-speed Main lines) | **~20 onboard units** | illustrative phased-rollout cap |
| Final-phase cap | **24 trains** | illustrative phased-rollout cap |
| Spec full-duplex capability | **44 onboard units** | RDSO KAVACH spec V4.0 |

These are exposed as `PEAK_LOAD_CAP_INITIAL`, `PEAK_LOAD_CAP_FINAL`,
`SPEC_DUPLEX_CAP` and applied via `StationDemandInputs.peak_load_cap` (CLI
`--peak-cap N`): a demand figure above the cap cannot occur operationally, so it
is clamped and flagged (`peak_load_capped`).

**Consistency check.** At a 24-train cap, the model gives `n_station = 19`,
`n_loco = 24`, `total = 43 ≤ 44` — i.e. **24 trains' full demand just fits one
pair**. A final-phase cap of 24 is therefore consistent with the 44-marker
single-pair capacity; the model lands on the same boundary independently.

**Note on the full-duplex "44".** The 44-onboard-unit figure is a *processing
capability* (full duplex = separate fS downlink / fM uplink frequencies). This
model treats `n_station` and `n_loco` as **distinct TDMA time markers that sum**
against the 44 *working markers* — a conservative reading. Whether the "44" is
fundamentally an *airtime* limit (the 44 markers used here) or a *processor*
limit that a duplex frame could exceed is worth confirming against the frame
structure; the summation reading never under-provisions, so it is safe either
way.

## 5. Estimating `N` when no count is given

If a chart gives no peak onboard count, `N` is estimated from track layout:

```
N ≈ berthing_tracks + directions · ⌈coverage_km / headway_km⌉
```

i.e. trains berthed in the yard plus trains in motion within RF coverage at the
signalling headway, both directions (`estimate_peak_locos`,
`test_estimate_peak_locos_from_layout`).

---

## 6. Section roll-up — minimum spectrum

Demand per station gives slot counts; the **number of frequency pairs** a section
needs is set by **frequency reuse**, not by slot demand. Two stations close
enough to interfere need different pairs. With a reuse window of `w` stations
(or an RF range over geographic positions), the worst-case simultaneously-
interfering group (clique) is `w + 1`, which is the spectrum **lower bound**:

```
freq_pairs_min = max interfering group size
```

For the synthetic section (`w = 4`) this is **5 pairs** — reproduced exactly by
the back-test, and matching the real section's spectrum. The roll-up also flags
any station whose demand exceeds one pair (`stations_exceeding_one_pair`), which
raises the realised spectrum above the lower bound when an over-capacity station
is split.

---

## 7. Why this is defensible

* **Every constant is sourced** — `traceability()` prints each value with its
  SPN/196 Annexure-C clause; nothing is a magic number.
* **Bottom-up, not assumed** — the profile burst is summed from spec sub-packet
  fields, so it moves with the spec.
* **Provably safe** — the mandatory-floor invariant is unit-tested across the
  operating range; the model never under-provisions.
* **Reproducible** — `tests/test_backtest_golden.py` re-derives every published
  number on a committed synthetic fixture in CI, with and without OR-Tools.
