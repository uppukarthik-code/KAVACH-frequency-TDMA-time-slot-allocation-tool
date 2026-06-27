# KAVACH Frequency–Time Allocation — Optimization Framework

A scalable, formally-specified framework for assigning **frequency pairs** and
**TDMA time slots** to KAVACH stations that **guarantees zero interference —
including third-order intermodulation (IM3) — while minimizing spectrum**.

> Companion code: `allocation_solver.py` (reference solver) and
> `im3_analysis.py` (IM3 calculator). Worked example output: see `README.md`.

---

## 1. Inputs and outputs

**Input (per station)** — exactly what the planner supplies:

| field | meaning |
|---|---|
| `station_id` | Stationary KAVACH unit id (in geographic order) |
| `n_station_slots` | number of **Stationary KAVACH** Tx slots required |
| `n_loco_slots` | number of **Loco KAVACH** Tx slots required (peak onboard units) |

Plus a **palette** of available duplex pairs (each pair = downlink `fS` + uplink
`fM`), the control/emergency carrier `f0`, and an **interference model**
(default: stations within `reuse_window` positions interfere; or an explicit
RF-range edge list).

**Output**
- one **atomic pair-colour** per station (spectrum-minimal, IM3-clean), and
- a **contiguous Stationary Tx window** + **non-adjacent Loco slots** per station,
clear of reserved slots and co-channel slot clashes.

---

## 2. Problem classification

Without IM3 this is a **Frequency Assignment Problem (FAP)** — a **list
multi-colouring** of the station interference graph:

| KAVACH constraint | Combinatorial structure |
|---|---|
| adjacent stations ≠ same pair | proper graph colouring |
| limited per-station frequency set | **list** colouring |
| many slots per station | **multi**-colouring (demand) |
| co-channel reuse needs disjoint slots | interval/slot scheduling |

IM3 (`2f₁−f₂` and `f₁+f₂−f₃`) couples **three** frequencies at once, lifting the
model to **3-uniform hypergraph colouring**:

> **List multi-colouring of an interference graph, augmented with an
> intermodulation hypergraph, minimizing spectrum.** NP-hard.

Each duplex pair is treated as **one atomic colour** owning carriers
`{fS_p, fM_p}` (current KAVACH practice); `f0` is shared.

---

## 3. Formal model (ILP / CP)

**Sets** stations `S`, interference edges `E`, channels `F⊂ℤ` (25 kHz grid),
slots `T`, reserved `T_res`. Pair-colours `P`; colour `p` owns integer channels
`{c(fS_p), c(fM_p)}`. Lists `L_s⊆P`. Demands `a_s` (station slots), `b_s` (loco
slots). IM3 triples `𝓘={(α,β,γ): 2α−β=γ}`, quads `𝓠={(α,β,γ,δ): α+β=γ+δ}`.

**Variables**
`y_{s,p}∈{0,1}` station `s` uses colour `p`; `z_{s,t}∈{0,1}` `s` active in slot
`t`; `u_p∈{0,1}` colour `p` used anywhere; `x_{s,p,t}=y_{s,p}∧z_{s,t}`.

**Constraints**

```
(C1) Σ_p y_{s,p} = 1 ,  y_{s,p}=0 if p∉L_s            one pair per station + list
(C2) y_{s,p} + y_{s',p} ≤ 1   ∀(s,s')∈E, p           adjacent stations differ
(C3) Σ_t z_{s,t} = a_s + b_s                          slot demand (multi-colour)
(C4) x_{s,p,t} + x_{s',p,t} ≤ 1  ∀(s,s')∈E            co-channel reuse ⇒ disjoint slots
(C5) loco slots of s pairwise non-adjacent            TAN 4.11 / SRS 3.4.5.9 (≥1 gap)
     station slots of s contiguous                    Stationary Tx window
     loco slots ≥150-200 ms before station window     SPN/196 17.14 (frame offset 0)
(C6) z_{s,t}=0  ∀ t∈T_res                             reserved (P1/P46 → "one timeslot")
(C7) IM3 hypergraph  (below)                          intermodulation-free
```

**C7 — IM3, two fidelity levels**

*Palette (global, conservative)* with `u_p ≥ y_{s,p}`, for the colour-set `K`
owning the carriers of each relation in `𝓘 ∪ 𝓠`:

```
Σ_{p∈K} u_p ≤ |K| − 1
```

*Spatial/time-aware (tight)* for mutually in-range station triples and slot `t`:

```
x_{s_a,α,t} + x_{s_b,β,t} + x_{s_c,γ,t} ≤ 2   ∀(α,β,γ)∈𝓘
```

TDMA slot-disjointness removes most triples automatically — this is **why `f0`
(emergency slots P47–P70) is safe**: it is never on-air with the normal-traffic
carriers, so its products cannot form. The solver therefore excludes `f0` from
the IM budget by default (`include_f0_in_im3=False`).

**Objective** — zero interference is *hard* (C1–C7); minimize spectrum:

```
min Σ_p u_p        (number of pairs)        or        min (f_max − f_min)  (span)
```

---

## 4. The structural result that makes it scalable — Sidon (B₂) palettes

On the integer grid the two IM3 conditions are
`2a−b=c ⟺ a+a=b+c` and `a+b−c=d ⟺ a+b=c+d`.

A **Sidon set** (B₂ set) has **all pairwise sums distinct**: `a+b=c+d ⇒ {a,b}={c,d}`.

- `a+a=b+c ⇒ {a,a}={b,c} ⇒ b=c=a` → **no two-tone product lands on a member**.
- `a+b=c+d ⇒ {a,b}={c,d}` → **no equal-sum quad → no three-tone product** either.

> **Theorem (informal).** If the channel palette `F` is a Sidon set on the
> 25 kHz grid, then *all* third-order intermodulation (both `2fᵢ−fⱼ` and
> `fᵢ+fⱼ−fₖ`) is impossible **by construction**, independent of the assignment.

This **collapses C7 entirely** — the problem reduces to ordinary list
multi-colouring (C1–C6), which is NP-hard but extremely well-tooled and scales
to national networks.

This is exactly why the legacy EXAMPLE palette fails: it is **not** Sidon —
`fS1, fS7, fS4` are an arithmetic progression (`fS1+fS4=2·fS7`) and
`fS1+fM1=fS2+fS4` is an equal-sum collision. Both are precisely the patterns a
Sidon set forbids.

**Trade-off:** a Sidon set of `k` channels needs span `Θ(k²)`, so IM3-freedom
costs spectrum *span*. For KAVACH (`k≈5–8`) this is negligible: `k²≪2560`
available channels.

---

## 5. Recommended two-layer architecture

**Layer 1 — Channel design (offline, once).**
Build the palette as a Sidon/B₂ set over 406–470 MHz, sizing the *combined*
`{fS}∪{fM}∪{f0}` set so uplink+downlink+emergency are **jointly** clean. This
makes zero-IM3 an **invariant of the palette**, not a per-allocation check.
→ `design_im3_free_palette()`, `is_sidon()`.

**Layer 2 — Assignment (per section, repeated).**
With an IM-free palette the residual is list multi-colouring + slot scheduling:
- **exact** CP-SAT/Gurobi for sections up to a few hundred stations,
- **decompose**: colour first, then per-colour slot scheduling (matches KAVACH's
  one-pair/many-slots structure),
- **heuristic at scale**: DSATUR + tabu/SA (the FAP work-horses).
→ `assign_colours()` (CP-SAT or exact backtracking), `assign_slots()`.

**Legacy-palette fallback.** If channels are frozen and non-Sidon, keep C7 in
its spatial/time-aware form, exploit TDMA slot-disjointness, and push residual
triples into the pattern (don't co-locate the offending pairs). The solver's
palette-mode C7 does this automatically (it simply refuses to use an IM-dirty
colour subset), which is why on the legacy 7-pair palette it returns a clean
sub-palette rather than failing.

---

## 6. How the code realizes the model

| Model element | Code |
|---|---|
| atomic colour = pair | `Pair`, `Problem.palette` |
| inputs (id, n_station, n_loco) | `solve_from_station_table(rows, …)` |
| interference graph | `Problem._window_edges()` / explicit `edges` |
| `𝓘 ∪ 𝓠` → forbidden colour-sets | `im3_forbidden_colour_sets()` |
| C1, C2, C7, objective | `assign_colours()` (CP-SAT `_assign_cpsat`, exact `_assign_backtrack`) |
| C3, C5, C6 (window + spaced loco) | `assign_slots()` |
| zero-interference proof | `validate()` |
| Layer-1 Sidon design | `design_im3_free_palette()`, `greedy_sidon()`, `is_sidon()` |

Run `python3 allocation_solver.py` for the worked example (spectrum-minimal
IM3-clean plan + a freshly designed Sidon palette).

---

## 7. Complexity & scaling notes

- General problem: NP-hard (graph + list + hypergraph colouring).
- **Sidon reduction** removes the hypergraph term → classical FAP; practically
  tractable with CP-SAT to hundreds of nodes, and with DSATUR/tabu beyond.
- Slot layer decomposes per colour-class; each is an interval/multi-colouring
  solvable greedily or exactly.
- Spectrum minimum is governed by the **interference radius** (`reuse_window`):
  larger radius → larger cliques → more pairs required. The solver returns the
  optimum for whatever radius the RF survey justifies.

---

## 9. Junctions & dense urban sections

The linear-corridor assumption (path-power graph, bounded treewidth, near-linear
solve) weakens where the track is not 1-D:

- **Junctions** are star/branch points → a station couples to several branches →
  larger local cliques → higher local chromatic demand.
- **Dense urban / suburban / yards** are effectively 2-D → the interference graph
  approaches a geometric (unit-disk) graph → larger cliques, growing treewidth,
  and possible **spectrum infeasibility** with a fixed palette. Slot demand also
  rises toward the 44-slot frame ceiling.

What still holds: the **model** (CP-SAT solves any graph), the **Sidon IM3
invariant** (topology-independent), and **decomposition** (cut at sparse block
sections; treat each junction/urban node as one denser subproblem of bounded
absolute size, solvable exactly or with DSATUR/tabu).

Two concrete levers (`dense_area.py`):

1. **Sectorization (TAN-compliant).** `sectorized_edges(coords, rf_range,
   antennas)` builds the interference graph from **directional antennas**: an
   edge exists only if each station's beam illuminates the other. At a junction,
   antennas pointing along their own branch do not couple across branches, so the
   omni clique is cut and the chromatic demand drops (demo: 4 → 3 pairs). This is
   the primary, compliant dense-area tool (cf. TAN 4.3 antenna staggering).

2. **Joint frequency × time-slot reuse (requires ISA deviation).**
   `solve_joint(...)` assigns each station a pair *and* its slots so that
   interfering stations never share a `(pair, slot)` cell — letting a dense
   cluster share **one** frequency across disjoint slots instead of demanding one
   frequency per station (demo: 6 mutually-interfering stations → 1 pair vs 6).
   **This deviates from TAN 4.11(1)** ("adjacent stations shall use different
   frequency pairs"); `allow_neighbor_reuse=True` is therefore gated and
   `compliance_note_joint()` marks it *DEVIATION — requires Project-ISA*. With
   `allow_neighbor_reuse=False` it reduces to the compliant colouring.

The honest split: in dense/junction areas the binding constraint is usually
**spectrum feasibility**, not solver speed. Sectorization and more spectrum are
the compliant resolutions; time-domain neighbour reuse buys spectrum only at the
cost of a TAN deviation.

## 10. Time-domain interference minimisation (slot staggering)

Frequency selection removes IM in the **frequency** domain. Any residual
third-order products (the ISA residuals) only physically **form when their
carriers are on-air in the same time slot** at mutually in-range stations — the
same reason `f0` is safe in the emergency slots. So a second, **time-domain**
lever exists: stagger the slots so the carriers of each residual relation never
coincide.

`stagger.py` does this (`stagger_slots()`): a CP-SAT slot placement that
**minimises the number of `(IM-relation × slot)` coincidences** among in-range
stations, subject to demand, contiguous window, non-adjacent loco, reserved
slots and SPN/196 17.14 offset-0. `count_im_coincidence()` is an independent
verifier/counter. Modelling note: station-active (window ∪ loco) is used as a
**conservative** proxy for carrier presence — reaching 0 coincidences therefore
**rigorously** means no product can form (a per-carrier refinement could squeeze
capacity-limited residuals further).

Result on the example: the default offset-0 schedule packs every station's loco comb
from P02, so IM-forming stations coincide heavily (**201** coincidence
slot-events). Staggering reduces this to **0** while preserving frame-offset-0 —
the three-tone residuals are **time-eliminated** (they cannot form) without
changing the licensed palette. In dense/junction or high-demand areas the frame
may be capacity-limited and the minimum is >0; the remainder (the least
achievable) goes to the ISA.

## 8. Open modelling choices (defaults in code)

1. **Atomic pair vs split fS/fM** — atomic (KAVACH practice). Splitting would
   roughly double the hypergraph but can save spectrum.
2. **f0 in IM budget** — excluded by default (time-separated). Verify f0 in its
   emergency-slot context separately.
3. **Interference model** — positional `reuse_window` by default; replace with a
   measured RF-range / path-loss edge list for production planning.
4. **Reserved slots** — align to amended clause C.3.2.9 ("one timeslot").
