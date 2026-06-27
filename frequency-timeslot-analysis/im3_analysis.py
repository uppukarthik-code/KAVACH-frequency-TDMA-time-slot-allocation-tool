#!/usr/bin/env python3
"""
KAVACH (Indian Railways TCAS/ATP) — Frequency Intermodulation (IM3) Analysis
============================================================================

STUDY / ANALYSIS TOOLING — NOT THE PRODUCTION IM3 ENGINE.
    This module is a standalone, float-based intermodulation *study* used to
    explore and rank candidate frequency selections. The AUTHORITATIVE,
    production IM3 engine is the integer-grid `model.im3_forbidden_colour_sets`
    (exact on the 25 kHz grid, used by the solver and validators). This file is
    not imported by the allocation pipeline and must not be relied on for
    compliance decisions. (Reconciling the two engines is tracked as audit
    DUP-3.)

Reproducible third-order intermodulation study for a KAVACH-style UHF mComm
frequency plan, performed on an ILLUSTRATIVE example section (synthetic
station IDs 10001-10022). All data here is example/synthetic, not a real
deployment.

Purpose
-------
Each KAVACH station is allotted a "frequency pair" = (downlink fS, uplink fM).
A common control/emergency centre frequency f0 is shared system-wide.
Third-order intermodulation products (2*fi - fj  and  fi + fj - fk) generated
in transmitter PAs, receiver front-ends or passive non-linear junctions ("rusty
bolt"/PIM) can fall on top of operating channels and jam reception
(-> RF packet loss / comms failure). This script enumerates those products
and ranks every candidate 5-pair selection.

Example data (synthetic / illustrative — NOT a real deployment):
  - 7 candidate frequency pairs (fS = Stn Tx, fM = Onboard Tx), all on the
    25 kHz channel grid (illustrative ~400 MHz band).
  - f0 = 402.350 MHz (control/emergency centre frequency).
  - An example pre-existing selection: {1, 2, 4, 5, 7}.

Run:
    python3 im3_analysis.py
"""

import itertools

# ---------------------------------------------------------------------------
# SOURCE FREQUENCY DATA (MHz)
# ---------------------------------------------------------------------------
# pair -> downlink (Station Tx)
fS = {1: 400.300, 2: 401.200, 3: 401.400, 4: 401.700,
      5: 400.000, 6: 400.575, 7: 401.000}
# pair -> uplink (Onboard/Loco Tx)
fM = {1: 402.600, 2: 403.600, 3: 404.250, 4: 404.525,
      5: 402.175, 6: 403.000, 7: 403.300}
# control / emergency centre frequency (shared, used only in emergency slots)
f0 = 402.350

# "Used" flag as per the example pre-existing selection
USED_NOW = {1, 2, 4, 5, 7}

CHANNEL_KHZ = 25.0   # channel spacing / bandwidth

# ---------------------------------------------------------------------------
# CORE IM3 ENGINE
# ---------------------------------------------------------------------------
def offset_khz(a, b):
    return round(abs(a - b) * 1000)

def carriers_for(pairs, include_f0=False):
    """Return {label: freq} for the carriers present for a given set of pairs."""
    c = {}
    for p in pairs:
        c[f"S{p}"] = fS[p]
        c[f"M{p}"] = fM[p]
    if include_f0:
        c["0"] = f0
    return c

def scan(carriers):
    """
    Enumerate 3rd-order IM products against the carrier set.
    Returns dict with lists of (formula, victim, product) for:
      on2  : two-tone  2*fi - fj  landing exactly on a carrier (0 kHz)
      on3  : three-tone fi + fj - fk landing exactly on a carrier (0 kHz)
      adj  : two-tone landing on the adjacent channel (25 kHz)
    All frequencies sit on the 25 kHz grid, so a "hit" is an exact (0 kHz)
    coincidence; "adjacent" is exactly one channel (25 kHz) away.
    """
    keys = list(carriers)
    f = carriers
    on2, on3, adj = [], [], []

    # two-tone 2*fi - fj
    for i in keys:
        for j in keys:
            if i == j:
                continue
            p = 2 * f[i] - f[j]
            for k in keys:
                o = offset_khz(p, f[k])
                if o == 0:
                    on2.append((f"2*{i}-{j}", k, round(p, 4)))
                elif o == CHANNEL_KHZ:
                    adj.append((f"2*{i}-{j}", k, round(p, 4)))

    # three-tone fi + fj - fk  (dedup symmetric i,j)
    seen = set()
    for i, j, k in itertools.permutations(keys, 3):
        if f[i] < f[j]:
            p = f[i] + f[j] - f[k]
            for m in keys:
                if offset_khz(p, f[m]) == 0:
                    key = tuple(sorted([i, j]) + [k, m])
                    if key not in seen:
                        seen.add(key)
                        on3.append((f"{i}+{j}-{k}", m, round(p, 4)))
    return {"on2": on2, "on3": on3, "adj": adj}

def min_adjacent_separation(pairs):
    """Best (max-min) adjacent fS separation achievable in a repeating cyclic
    pattern -> proxy for TAN 4.11 'prefer wider separation'. Returns (khz, order)."""
    best = None
    for perm in itertools.permutations(pairs):
        gaps = [abs(fS[perm[i]] - fS[perm[(i + 1) % len(perm)]])
                for i in range(len(perm))]
        mg = min(gaps)
        if best is None or mg > best[0]:
            best = (mg, perm)
    return round(best[0] * 1000), list(best[1])

# ---------------------------------------------------------------------------
# REPORTS
# ---------------------------------------------------------------------------
def grid_check():
    allf = list(fS.values()) + list(fM.values()) + [f0]
    ok = all(abs((x / 0.025) - round(x / 0.025)) < 1e-6 for x in allf)
    print(f"All frequencies on 25 kHz grid: {ok}")
    print(f"  fS span {min(fS.values())}-{max(fS.values())} MHz | "
          f"fM span {min(fM.values())}-{max(fM.values())} MHz | f0 {f0} MHz\n")

def rank(include_f0, by_f0_first=False):
    rows = []
    for combo in itertools.combinations(sorted(fS), 5):
        r = scan(carriers_for(combo, include_f0))
        hit0 = ([x for x in r["on2"] if x[1] == "0"] +
                [x for x in r["on3"] if x[1] == "0"]) if include_f0 else []
        swaps = len(set(combo) - USED_NOW)
        rows.append((len(r["on2"]), len(r["on3"]), len(r["adj"]),
                     len(hit0), swaps, list(combo)))
    if by_f0_first:
        rows.sort(key=lambda x: (x[3], x[0], x[1], x[2], x[4]))
    else:
        rows.sort(key=lambda x: (x[0], x[1], x[2], x[4]))

    title = "WITH f0 (worst-case: all carriers simultaneous)" if include_f0 \
            else "WORKING CARRIERS ONLY (fS + fM)"
    print(f"=== Ranking of all 21 five-pair selections — {title} ===")
    hdr = "rk | pairs           | 2tone | 3tone | adj | "
    hdr += "onF0 | " if include_f0 else ""
    hdr += "swaps"
    print(hdr)
    print("-" * len(hdr))
    for n, x in enumerate(rows, 1):
        note = " <= current" if set(x[5]) == USED_NOW else ""
        line = f"{n:>2} | {str(x[5]):15} | {x[0]:^5} | {x[1]:^5} | {x[2]:^3} | "
        line += f"{x[3]:^4} | " if include_f0 else ""
        line += f"{x[4]:^5}{note}"
        print(line)
    print()
    return rows

def detail(pairs, include_f0=False, label=""):
    r = scan(carriers_for(pairs, include_f0))
    mg, order = min_adjacent_separation(pairs)
    print(f"--- {label or 'Set'} {sorted(pairs)} "
          f"({'with f0' if include_f0 else 'working carriers'}) ---")
    print(f"  best cyclic pattern: {order}  | min adjacent fS gap: {mg} kHz")
    print(f"  on-channel 2-tone ({len(r['on2'])}): "
          f"{[f'{a}->{b}' for a, b, _ in r['on2']] or 'NONE'}")
    print(f"  on-channel 3-tone ({len(r['on3'])}): "
          f"{[f'{a}->{b}' for a, b, _ in r['on3']] or 'NONE'}")
    print(f"  adjacent 25 kHz ({len(r['adj'])}): "
          f"{[f'{a}->{b}' for a, b, _ in r['adj']] or 'none'}")
    print()

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    grid_check()

    print("########## DOWNLINK + UPLINK (the carriers that are time-coincident) ##########\n")
    rank(include_f0=False)

    print("########## DETAIL: current vs recommended ##########\n")
    detail([1, 2, 4, 5, 7], include_f0=False, label="example pre-existing")
    detail([1, 2, 4, 5, 6], include_f0=False, label="RECOMMENDED (1 retune)")
    detail([2, 3, 4, 5, 6], include_f0=False, label="Cleanest (2 retunes)")

    print("########## WITH f0 (pessimistic 'all simultaneous' model) ##########")
    print("NOTE: f0 is transmitted only in the emergency slots P47-P70, which are")
    print("time-separated from the normal-traffic slots P02-P45. Carriers that are")
    print("never on-air together cannot intermodulate, so the f0 columns below")
    print("OVERSTATE the real risk and are shown only for completeness.\n")
    rank(include_f0=True, by_f0_first=True)
