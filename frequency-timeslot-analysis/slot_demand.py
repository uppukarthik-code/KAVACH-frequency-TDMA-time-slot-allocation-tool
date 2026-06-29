#!/usr/bin/env python3
"""
slot_demand.py — RDSO-traceable KAVACH slot-demand calculator.

Computes, per station, the number of **Stationary KAVACH Tx slots** (downlink)
and **Onboard/Loco KAVACH Tx slots** (uplink) required in the SPN/196 TDMA
frame, from traffic + yard inputs.

Design goals (for RDSO / Railway-Board scrutiny and zone-wide sharing):
  * DEFENSIBLE  — every constant is cited to RDSO/SPN/196/2020 Annexure-C
                  (Multiple Access Scheme & Protocol). No tuned/opaque numbers.
  * SAFE        — it is a WORST-CASE airtime upper bound that ROUNDS UP, so it
                  never under-provisions a safety-critical link.
  * SIMPLE      — one linear airtime formula, reproducible in a spreadsheet;
                  reduces to the spec rule "one packet per loco per cycle".
  * TRACEABLE   — `traceability()` prints every constant against its clause.

It replaces the legacy spreadsheet heuristic
    n_station = ROUNDUP( (S*120 + (L-S)*40 + 100) / 66 , 0 )
whose constants (120, 40, 100, 66) are not traceable to the spec. This module
keeps the SAME airtime structure but with spec-cited bytes, so it agrees with
approved practice on light stations and is correct (not optimistic) on busy
ones.

Output (n_station, n_loco) feeds directly into the allocator (`Problem`).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

# ===========================================================================
# SPEC-CITED CONSTANTS  (RDSO/SPN/196/2020 Annexure-C, v4.0)
# Each value is traceable to a clause / packet field table — see traceability().
# ===========================================================================
WORKING_MARKERS   = 44    # regular Station<->Loco markers M1..M44 = P2..P45   (C.3.2.1)
RESERVED_MARKERS  = 2     # P1, P46                                            (C.3.2.9)
MARKER_PAYLOAD_B  = 54    # 432 bits per marker                               (C.3.2)
DL_HEADER_B       = 16    # Station->Onboard packet header (p.13; sample C.3.2.13 = 16 B)
DL_MA_B           = 19    # Movement Authority sub-packet (SUB_PKT_TYPE 0000, pp.15-21)
DL_BROADCAST_B    = 16    # common / broadcast overhead per cycle             (p.13)
UL_REGULAR_B      = 29    # Onboard->Station Regular packet = 230 bits        (p.38)
PAYLOAD_EFF       = 1.0   # usable fraction of a marker (1.0 = gross 54 B; set
                          # <1 to add framing/guard/FEC margin)               (engineering)

# Derived (kept explicit so a reviewer can check the arithmetic by hand)
B_REG   = DL_HEADER_B + DL_MA_B           # 35 B  : mandatory per-loco per-cycle (MA every cycle)
USABLE_B = PAYLOAD_EFF * MARKER_PAYLOAD_B  # bytes carried by one marker

# ===========================================================================
# OPERATIONAL PEAK-LOAD CAPS  (illustrative policy limits on onboard units per
# Stationary KAVACH, set BELOW the spec airtime ceiling for phased commissioning).
# The spec full-duplex capability is 44 onboard units (RDSO KAVACH spec V4.0);
# operators may cap supervised trains lower during phased commissioning. The
# example values below (20 initial, 24 final) are ILLUSTRATIVE phased-rollout
# caps, configurable per operator. They are TRAIN-COUNT caps, not airtime; the
# TDMA airtime ceiling remains WORKING_MARKERS (44 slots) per pair.
# ===========================================================================
PEAK_LOAD_CAP_INITIAL = 20   # illustrative initial-phase cap (Main lines)
PEAK_LOAD_CAP_FINAL   = 24   # illustrative final-phase cap on equipped trains
SPEC_DUPLEX_CAP       = 44   # RDSO KAVACH spec V4.0 full-duplex capability
PEAK_LOAD_CAPS = {"initial": PEAK_LOAD_CAP_INITIAL,
                  "final": PEAK_LOAD_CAP_FINAL,
                  "spec": SPEC_DUPLEX_CAP}

# ===========================================================================
# BOTTOM-UP TRACK-PROFILE SIZE  (no tuned constant — summed from spec fields)
# Each profile sub-packet = SUB_PKT_TYPE(4) + SUB_PKT_LENGTH(7) + COUNT field +
# COUNT x (per-entry fields), byte-aligned and capped at 128 B (pp.22-31).
# PROFILE_FIELDS[name] = (base_bits, per_entry_bits, spec source)
# ===========================================================================
SUBPKT_CAP_B = 128        # max sub-packet size (SUB_PKT_LENGTH, pp.22-31)
PROFILE_FIELDS = {
    # name : (base_bits, per_entry_bits, source)
    "ssp":  (4 + 7 + 5, 15 + 1 + 6,            "Static Speed Profile pp.22-23"),   # dist+class+value
    "grad": (4 + 7 + 5, 15 + 1 + 5,            "Gradient Profile p.23"),           # dist+gdir+value
    "lc":   (4 + 7 + 5, 15 + 10 + 3 + 1 + 3 + 1 + 2, "LC Gate Profile pp.24-25"),  # dist+id+suffix+manning+class+whEn+whType
    "to":   (4 + 7 + 2, 5 + 15 + 12,           "Turnout Speed Profile pp.26-27"),  # speed+diffdist+reldist
    "tc":   (4 + 7 + 4, 4 + 15 + 15,           "Track Condition pp.29-30"),        # type+start+length
    "tsr":  (4 + 7 + 2 + 5, 8 + 15 + 15 + 1 + 6 + 2, "TSR Profile pp.30-31"),      # id+dist+len+class+speed+whistle
    "tli":  (4 + 7 + 4 + 6, 11 + 10 + 1,       "Tag Linking pp.27-29"),            # per route tag: distnxt+tagid+dupdir
}
# A documented "typical mid-size yard" element set; used only as the DEFAULT
# profile burst when a station's real yard counts are not supplied. Replacing
# the tuned 85 B with this transparent sum.
DEFAULT_PROFILE_COUNTS = {"ssp": 4, "grad": 4, "lc": 1, "to": 2, "tc": 1,
                          "tsr": 0, "tli": 10}


def profile_bytes(counts: dict) -> int:
    """Bottom-up track-profile burst (bytes) for one loco, summed over the
    sub-packets present, using EXACT spec field sizes. Byte-aligned, each
    sub-packet capped at 128 B (pp.22-31). No tuned constants."""
    total = 0
    for name, (base, per, _src) in PROFILE_FIELDS.items():
        n = counts.get(name, 0)
        if n <= 0:
            continue
        b = min(math.ceil((base + per * n) / 8), SUBPKT_CAP_B)
        total += b
    return total


# The profile burst used by station_tx_slots when no per-station counts are
# given: the bottom-up sum for DEFAULT_PROFILE_COUNTS (now derived, not assumed).
DL_PROFILE_B = profile_bytes(DEFAULT_PROFILE_COUNTS)


@dataclass
class StationDemandInputs:
    """All inputs for one station. `peak_locos` is the dominant driver; the rest
    refine the airtime or let `peak_locos` be estimated from the track layout."""
    # --- traffic (dominant driver) ---
    peak_locos: Optional[int] = None     # N: peak concurrent KAVACH locos in RF coverage
    leading_locos: Optional[int] = None  # locos transmitting every cycle (default = peak_locos)
    profiled_locos: Optional[int] = None # locos given a FULL profile burst per cycle
                                         # (default = last_stop_signals)
    last_stop_signals: int = 2           # default profiled-loco count (approach signals)
    profile_counts: Optional[dict] = None  # per-station yard counts -> bottom-up
                                         # profile burst (keys: ssp/grad/lc/to/tc/
                                         # tsr/tli). None -> DEFAULT_PROFILE_COUNTS.
    # --- optional: estimate peak_locos from layout instead of supplying it ---
    berthing_tracks: int = 0             # running/loop lines that can hold a train
    directions: int = 0                  # approach directions (1 plain, up to ~6 junction)
    coverage_km: float = 0.0             # RF coverage each side (station boundary, <=~10 km)
    headway_km: float = 0.0              # minimum train spacing / block length
    # --- optional: Railway-Board operational peak-load cap (trains supervised) ---
    peak_load_cap: Optional[int] = None  # clamp supervised trains to this many
                                         # (e.g. PEAK_LOAD_CAP_FINAL=24). None =
                                         # uncapped. Enforced via Exit Tags in
                                         # the field (operator commissioning policy).


def estimate_peak_locos(berthing_tracks: int, directions: int,
                        coverage_km: float, headway_km: float) -> int:
    """Concurrent locos in RF range = trains held in the yard + trains approaching
    within coverage on each direction. A transparent capacity estimate; the
    headway comes from the operating plan / section capacity."""
    approaching = (directions * math.ceil(coverage_km / headway_km)
                   if headway_km > 0 else 0)
    return berthing_tracks + approaching


def station_tx_slots(peak_locos: int, profiled_locos: int,
                     profile_burst_b: int = DL_PROFILE_B,
                     broadcast_b: int = DL_BROADCAST_B) -> int:
    """Downlink slots: airtime upper bound, rounded up.
        bytes/cycle = N*(header+MA) + profiled*(profile burst) + broadcast
        slots       = ceil(bytes/cycle / usable bytes per marker)
    Every served loco gets header+MA every cycle (spec C.4.2 + MA table); the
    `profiled` locos additionally carry their track-profile sub-packets
    (`profile_burst_b`, defaulting to the bottom-up DL_PROFILE_B)."""
    if peak_locos <= 0:
        return 0                              # no locos served -> no slot demand
    profiled = min(profiled_locos, peak_locos)
    dl_bytes = peak_locos * B_REG + profiled * profile_burst_b + broadcast_b
    return math.ceil(dl_bytes / USABLE_B)


def loco_tx_slots(leading_locos: int) -> int:
    """Uplink slots: each *leading* loco sends one regular packet per cycle
    (C.3.2.7); a 230-bit packet fits one marker, so one slot per leading loco.
    Non-leading/isolation locos transmit every 30-240 s and add a negligible
    fraction, so this is a safe upper bound."""
    per_loco = math.ceil(UL_REGULAR_B / USABLE_B)   # = 1 (29 B <= one marker)
    return leading_locos * per_loco


def slot_demand(inp: StationDemandInputs) -> dict:
    """Full per-station result: (n_station, n_loco), capacity check and the
    number of duplex frequency pairs implied."""
    N = inp.peak_locos
    if N is None:
        N = estimate_peak_locos(inp.berthing_tracks, inp.directions,
                                inp.coverage_km, inp.headway_km)
    if N < 0:
        raise ValueError("peak_locos must be >= 0")
    # Railway-Board operational cap on supervised trains (enforced via Exit Tags
    # in the field; operator policy). A demand figure above the cap cannot
    # occur operationally, so clamp and flag it.
    demand_locos, capped = N, False
    if inp.peak_load_cap is not None and N > inp.peak_load_cap:
        if inp.peak_load_cap < 0:
            raise ValueError("peak_load_cap must be >= 0")
        demand_locos, capped = inp.peak_load_cap, True
    lead = inp.leading_locos if inp.leading_locos is not None else demand_locos
    prof = inp.profiled_locos if inp.profiled_locos is not None else inp.last_stop_signals
    burst = profile_bytes(inp.profile_counts) if inp.profile_counts else DL_PROFILE_B

    n_station = station_tx_slots(demand_locos, prof, profile_burst_b=burst)
    n_loco = loco_tx_slots(lead)
    total = n_station + n_loco
    pairs = math.ceil(total / WORKING_MARKERS) if total else 1
    return {
        "peak_locos": N,                  # requested/estimated peak
        "supervised_locos": demand_locos,  # after operational cap (= peak if uncapped)
        "peak_load_capped": capped,        # True if the cap reduced the figure
        "n_station": n_station,
        "n_loco": n_loco,
        "total_slots": total,
        "fits_one_pair": total <= WORKING_MARKERS,
        "freq_pairs_min": pairs,
    }


def demand_table(stations: dict) -> dict:
    """stations: {station_id: StationDemandInputs} -> {station_id: result dict}.
    The (n_station, n_loco) can be fed straight into model.Problem."""
    return {sid: slot_demand(inp) for sid, inp in stations.items()}


def to_problem_slots(stations: dict):
    """Convenience: {id: StationDemandInputs} -> (sta_slots, loco_slots) dicts
    ready for model.Problem(stations, sta_slots, loco_slots, ...)."""
    tbl = demand_table(stations)
    sta = {sid: r["n_station"] for sid, r in tbl.items()}
    loco = {sid: r["n_loco"] for sid, r in tbl.items()}
    return sta, loco


def _max_interfering_group(ids: list, reuse_window: Optional[int] = None,
                           positions: Optional[dict] = None,
                           rf_range_km: Optional[float] = None) -> int:
    """Largest set of MUTUALLY in-range stations (a clique of the interference
    graph). Every station in such a group needs a DISTINCT frequency pair, so
    this is the minimum-pairs (spectrum) lower bound for the section.

    Both supported interference models give an *interval graph*, so the clique
    is computed exactly in O(n log n):
      * reuse_window: stations within W positions interfere -> max run = W+1.
      * positions+rf_range_km: max stations inside any window of width rf_range.
    """
    n = len(ids)
    if n == 0:
        return 0
    if positions is not None and rf_range_km is not None:
        pts = sorted(positions[i] for i in ids if i in positions)
        best, j = 0, 0
        for i in range(len(pts)):
            while pts[i] - pts[j] > rf_range_km:
                j += 1
            best = max(best, i - j + 1)
        return best
    if reuse_window is not None:
        return min(reuse_window + 1, n)
    return n  # no model given -> worst case (all interfere)


def section_rollup(stations: dict, *, reuse_window: int = 4,
                   positions: Optional[dict] = None,
                   rf_range_km: Optional[float] = None) -> dict:
    """Per-SECTION spectrum-demand roll-up.

    stations: {station_id: StationDemandInputs}
    Aggregates the per-station demand and reports `freq_pairs_min` — the minimum
    number of duplex frequency pairs the section needs, i.e. the size of the
    largest mutually-interfering group of stations (each needs a distinct pair).
    IM3 / palette constraints can only push the actual spectrum HIGHER, so this
    is a safe lower bound; the allocator computes the exact figure.

    Also flags any station whose own demand exceeds one pair's 44 markers
    (infeasible without splitting -> needs review)."""
    tbl = demand_table(stations)
    ids = list(tbl)
    tot_sta = sum(r["n_station"] for r in tbl.values())
    tot_loco = sum(r["n_loco"] for r in tbl.values())
    over = [sid for sid, r in tbl.items() if r["total_slots"] > WORKING_MARKERS]
    clique = _max_interfering_group(ids, reuse_window, positions, rf_range_km)
    return {
        "n_stations": len(ids),
        "total_station_slots": tot_sta,
        "total_loco_slots": tot_loco,
        "total_slots": tot_sta + tot_loco,
        "peak_station_total_slots": max((r["total_slots"] for r in tbl.values()), default=0),
        "max_interfering_group": clique,
        "freq_pairs_min": clique,            # spectrum lower bound (interference)
        "stations_exceeding_one_pair": over,  # feasibility flag (demand > 44 markers)
        "feasible_per_station": not over,
    }


def traceability() -> list:
    """Return (quantity, value, unit, RDSO/SPN/196 Annexure-C source) rows so a
    reviewer can audit every constant. This is the defensibility artefact."""
    return [
        ("Working markers (M1..M44 = P2..P45)", WORKING_MARKERS, "slots", "C.3.2.1"),
        ("Reserved markers (P1, P46)", RESERVED_MARKERS, "slots", "C.3.2.9"),
        ("Marker payload (432 bits)", MARKER_PAYLOAD_B, "bytes", "C.3.2"),
        ("Downlink packet header", DL_HEADER_B, "bytes", "header table p.13 / sample C.3.2.13"),
        ("Movement Authority sub-packet", DL_MA_B, "bytes", "MA sub-packet fields pp.15-21"),
        ("Header + MA (mandatory per loco/cycle)", B_REG, "bytes", "C.4.2 (one packet per loco; MA every cycle)"),
        (f"Track-profile burst (bottom-up, default {DEFAULT_PROFILE_COUNTS})",
         DL_PROFILE_B, "bytes", "sum of sub-packets below (each <=128 B)"),
        ("Broadcast / common overhead", DL_BROADCAST_B, "bytes", "header table p.13"),
        ("Onboard->Station regular packet", UL_REGULAR_B, "bytes", "total row p.38 (230 bits)"),
        ("Payload efficiency factor", PAYLOAD_EFF, "-", "engineering margin (1.0 = gross)"),
        ("Peak-load cap: initial phase (Main lines)", PEAK_LOAD_CAP_INITIAL,
         "trains", "illustrative phased-rollout cap (operator policy)"),
        ("Peak-load cap: final phase", PEAK_LOAD_CAP_FINAL,
         "trains", "illustrative final-phase cap (operator policy)"),
        ("Spec V4.0 capability (full duplex)", SPEC_DUPLEX_CAP,
         "units", "RDSO KAVACH spec V4.0 (full duplex)"),
        ("Control / emergency frequency f0", 402.350, "MHz",
         "KAVACH UHF control channel (example value)"),
    ] + [
        (f"  profile sub-packet '{name}' (base + {per}/entry)", base, "bits", src)
        for name, (base, per, src) in PROFILE_FIELDS.items()
    ]


def _demo():
    print("KAVACH slot-demand calculator — spec-traceable (RDSO/SPN/196 Annexure-C)\n")
    print("Traceability of constants:")
    for q, v, u, src in traceability():
        print(f"  {q:<42} {v:>5} {u:<6} [{src}]")
    print()
    # a worked example station
    inp = StationDemandInputs(peak_locos=10, last_stop_signals=2)
    r = slot_demand(inp)
    print(f"Example: 10 concurrent locos, 2 approach signals ->")
    print(f"  Stationary Tx slots = {r['n_station']}, Loco Tx slots = {r['n_loco']}, "
          f"total = {r['total_slots']} / {WORKING_MARKERS} per pair "
          f"(needs {r['freq_pairs_min']} pair)")
    # layout-estimated example
    inp2 = StationDemandInputs(peak_locos=None, berthing_tracks=3, directions=2,
                               coverage_km=10.0, headway_km=3.0, last_stop_signals=2)
    r2 = slot_demand(inp2)
    print(f"Layout-estimated (3 berthing tracks, 2 dirs, 10 km cover, 3 km headway):")
    print(f"  estimated peak locos = {r2['peak_locos']} -> "
          f"n_station = {r2['n_station']}, n_loco = {r2['n_loco']}")


if __name__ == "__main__":
    _demo()
