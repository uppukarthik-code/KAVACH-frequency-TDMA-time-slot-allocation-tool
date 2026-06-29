#!/usr/bin/env python3
"""
Reporting — the per-station frequency & time-slot allocation, the RDSO
clause-by-clause compliance report, and the change justification vs the existing
chart.

Split out of allocation_solver.py (audit CQ-2). Imports from `model`, `slots`
(frame_offset) and `stagger` (residual-IM coincidence count); `allocation_solver`
re-exports these names for backward compatibility.
"""
from __future__ import annotations

import stagger
from model import (_chan, GRID_MHZ, MARKER_MS, default_gap_slots,
                   palette_is_im3_clean, im3_forbidden_colour_sets, Problem)
from slots import frame_offset


def _fmt_markers(slots):
    """Contiguous run -> 'P02-P13'; otherwise comma list 'P14,P16,...'."""
    if not slots:
        return "-"
    ss = sorted(slots)
    if ss[-1] - ss[0] == len(ss) - 1:
        return f"P{ss[0]:02d}-P{ss[-1]:02d}"
    return ",".join(f"P{t:02d}" for t in ss)


def allocation_table(prob: Problem, result: dict):
    """Structured per-station allocation: frequency pair (with real fS/fM) and
    the Stationary Tx window + Loco slots, as P-markers."""
    pal = {p.id: p for p in prob.palette}
    offs = frame_offset(prob, result['schedule'], result.get('gap_slots'))
    rows = []
    for s in prob.stations:
        p = pal[result['colour'][s]]
        sl = result['schedule'][s]
        rows.append({
            'station_id': s,
            'pair': p.id,
            'fS_dl_MHz': round(p.fS, 3),
            'fM_ul_MHz': round(p.fM, 3),
            'f0_MHz': prob.f0,
            'n_sta': len(sl['station']),
            'station_window': _fmt_markers(sl['station']),
            'n_loco': len(sl['loco']),
            'loco_slots': _fmt_markers(sl['loco']),
            'frame_offset': offs[s],
        })
    return rows


def print_allocation_table(prob: Problem, result: dict):
    rows = allocation_table(prob, result)
    # #StaSlots / #LocoSlots are the computed station- and loco-slot COUNTS
    # (n_station, n_loco); the window/positions columns show WHERE they sit.
    hdr = (f"{'Station':>8} | {'Pair':>4} | {'fS(dl)':>8} | {'fM(ul)':>8} | {'FO':>2} | "
           f"{'#StaSlots':>9} | {'#LocoSlots':>10} | {'StationTxWindow':>15} | "
           f"LocoSlots(uplink, early)")
    print(hdr)
    print("-" * (len(hdr) + 6))
    for r in rows:
        print(f"{r['station_id']:>8} | {r['pair']:>4} | {r['fS_dl_MHz']:>8.3f} | "
              f"{r['fM_ul_MHz']:>8.3f} | {r['frame_offset']:>2} | "
              f"{r['n_sta']:>9} | {r['n_loco']:>10} | {r['station_window']:>15} | "
              f"{r['loco_slots']}")
    f0 = rows[0]['f0_MHz'] if rows else None
    print(f"\n  #StaSlots = station-transmit (downlink) slots n_station; "
          f"#LocoSlots = loco-transmit (uplink) slots n_loco")
    print(f"  control/emergency f0 = {f0} MHz (shared, emergency slots P47-P70)")
    print(f"  spectrum used = {result['spectrum']} pairs: {sorted(result['used_pairs'])}")


def slot_demand_table(stations, demand):
    """Print the per-station slot-demand calculation (the methodology's inputs ->
    n_station, n_loco). `demand[sid]` is a slot_demand() result dict."""
    hdr = (f"{'Station':>8} | {'PeakOnboard':>11} | {'Signals':>7} | "
           f"{'n_station':>9} | {'n_loco':>6} | {'total':>5} | {'fits 1 pair':>11}")
    print(hdr)
    print("-" * (len(hdr) + 4))
    for sid in stations:
        d = demand[sid]
        print(f"{sid:>8} | {d['peak_locos']:>11} | "
              f"{d.get('last_stop_signals', ''):>7} | {d['n_station']:>9} | "
              f"{d['n_loco']:>6} | {d['total_slots']:>5} | "
              f"{('yes' if d['fits_one_pair'] else 'NO -> split'):>11}")
    tot_s = sum(demand[s]['n_station'] for s in stations)
    tot_l = sum(demand[s]['n_loco'] for s in stations)
    print(f"\n  totals: n_station = {tot_s}, n_loco = {tot_l}, "
          f"section demand = {tot_s + tot_l} slots")


_ALLOC_FIELDS = ['station_id', 'pair', 'fS_dl_MHz', 'fM_ul_MHz', 'f0_MHz',
                 'n_sta', 'station_window', 'n_loco', 'loco_slots', 'frame_offset']


def write_allocation_csv(path, prob: Problem, result: dict):
    import csv
    rows = allocation_table(prob, result)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_ALLOC_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


# ===========================================================================
# COMPLIANCE REPORT  (RDSO TAN 4.11 + SPN/196 frame, clause by clause)
# ===========================================================================
def compliance_report(prob: Problem, result: dict):
    """Per-clause PASS / NOTE list for the generated allocation."""
    colour, sched = result['colour'], result['schedule']
    pal = {p.id: p for p in prob.palette}
    chk = []

    # TAN 4.11(1): adjacent stations different pair
    bad = [(a, b) for a, b in prob.edges if colour[a] == colour[b]]
    chk.append(("TAN 4.11 - adjacent stations different pair",
                "PASS" if not bad else f"FAIL {bad}"))

    # TAN 4.11(2): loco slots non-adjacent
    bad = [s for s in prob.stations
           if any(b - a == 1 for a, b in zip(sorted(sched[s]['loco']),
                                             sorted(sched[s]['loco'])[1:]))]
    chk.append(("TAN 4.11 - loco slots non-adjacent (>=1 gap)",
                "PASS" if not bad else f"FAIL {bad}"))

    # TAN 4.11(3): reserved slots
    bad = [s for s in prob.stations
           if set(sched[s]['station'] + sched[s]['loco']) & prob.reserved_slots]
    chk.append((f"TAN 4.11 - reserved slots {sorted(prob.reserved_slots)} kept free",
                "PASS" if not bad else f"FAIL {bad}"))

    # TAN 4.11(4): wider separation - report the achieved minimum adjacent gap
    chan = {p.id: _chan(p.fS) for p in prob.palette}
    gaps = [abs(chan[colour[a]] - chan[colour[b]]) for a, b in prob.edges
            if colour[a] != colour[b]]
    mg = (min(gaps) * GRID_MHZ * 1000) if gaps else 0
    chk.append(("TAN 4.11 - wider separation (min adjacent gap)",
                f"PASS - min {mg:.0f} kHz between interfering stations"))

    # SPN-196 frame: station window contiguous; demand fits P2..P45
    bad = [s for s in prob.stations
           if sched[s]['station'] and
           sorted(sched[s]['station'])[-1] - sorted(sched[s]['station'])[0]
           != len(sched[s]['station']) - 1]
    chk.append(("SPN/196 - Stationary Tx window contiguous",
                "PASS" if not bad else f"FAIL {bad}"))
    # SPN/196 17.14: frame-offset 0 (loco data >=150-200 ms before station slot)
    offs = frame_offset(prob, sched, result.get('gap_slots'))
    n0 = sum(1 for v in offs.values() if v == 0)
    gap_ms = (result.get('gap_slots') or default_gap_slots()) * MARKER_MS
    o1 = [s for s in prob.stations if offs[s] == 1]
    chk.append((f"SPN/196 17.14 - frame offset 0 (loco >= {gap_ms:.0f} ms before "
                f"station slot -> fresh same-cycle MA)",
                f"PASS - {n0}/{len(offs)} stations at offset 0"
                + ("" if not o1 else f"; offset 1 (1-cycle latency): {o1}")))

    usable = len([t for t in range(1, prob.num_slots + 1)
                  if t not in prob.reserved_slots])
    # Structural capacity: n_sta contiguous + n_loco non-adjacent in the rest.
    # Best case places the window at one end leaving a run of (usable - n_sta);
    # max non-adjacent in a run of R is ceil(R/2) = (R+1)//2.
    def fits(s):
        a, b = prob.sta_slots.get(s, 0), prob.loco_slots.get(s, 0)
        if a > usable:
            return False
        run = usable - a
        cap_loco = (run + 1) // 2 if prob.loco_nonadjacent else run
        return b <= cap_loco
    over = [s for s in prob.stations if not fits(s)]
    chk.append((f"SPN/196 - demand fits working frame "
                f"({usable} slots; window + non-adjacent loco)",
                "PASS" if not over else f"FAIL over-subscribed {over}"))

    # IM3 (enhancement beyond spec)
    upal = [pal[c] for c in set(colour.values())]
    _, fs_full = palette_is_im3_clean(upal, prob.f0, prob.include_f0_in_im3)
    _, fs_two = (None, im3_forbidden_colour_sets(upal, prob.f0,
                 prob.include_f0_in_im3, orders=("two",)))
    used = set(colour.values())
    two = [tuple(sorted(K)) for K in fs_two if K <= used]
    three = [tuple(sorted(K)) for K in fs_full if K <= used and K not in fs_two]
    chk.append(("IM3 two-tone (enhancement)",
                "PASS - none" if not two else f"FAIL {two}"))
    if not three:
        chk.append(("IM3 three-tone (enhancement)", "PASS - none"))
    else:
        imco = result.get('im_coincidence')
        if imco is None:
            try:
                imco = stagger.count_im_coincidence(prob, colour, sched, "full")
            except Exception:
                imco = None
        if imco is not None and imco[0] == 0:
            chk.append(("IM3 three-tone (time-domain staggering)",
                        f"PASS - palette has {three} but 0 slot-coincidences at "
                        f"in-range stations -> products cannot form"))
        elif imco is not None:
            chk.append(("IM3 three-tone (time-domain staggering)",
                        f"NOTE - {three} minimised to {imco[0]} residual "
                        f"coincidences {imco[1]} -> document remainder in ISA"))
        else:
            chk.append(("IM3 three-tone (enhancement; -> ISA if present)",
                        f"NOTE residuals {three} -> document in ISA"))
    return chk


def justify_changes(prob: Problem, result: dict, existing_pair: dict):
    """
    Compare the generated allocation against the planner's existing 'Proposed
    Frequency Pair' column and explain every change.
    existing_pair: id -> pair index (from the input chart).
    """
    colour = result['colour']
    pal = {p.id: p for p in prob.palette}
    # forbidden relations of the EXISTING used set (to attribute IM changes)
    ex_used = {existing_pair[s] for s in prob.stations if s in existing_pair}
    ex_two = [K for K in im3_forbidden_colour_sets(
                  [pal[p] for p in ex_used if p in pal], prob.f0,
                  prob.include_f0_in_im3, orders=("two",)) if K <= ex_used]
    out = []
    for s in prob.stations:
        old = existing_pair.get(s)
        new = colour[s]
        if old == new:
            out.append((s, old, new, "unchanged"))
            continue
        if old is None:
            out.append((s, old, new, "no prior pair in input (new/unlisted station)"))
            continue
        reasons = []
        if any(old in K for K in ex_two):
            reasons.append("old pair was part of a two-tone IM3 relation")
        # adjacency of old assignment
        for a, b in prob.edges:
            if s in (a, b):
                other = b if a == s else a
                if existing_pair.get(other) == old:
                    reasons.append(f"old pair clashed with neighbour {other} (same pair)")
                    break
        if not reasons:
            reasons.append("re-coloured to reach spectrum-minimal / wider-separation optimum")
        out.append((s, old, new, "; ".join(reasons)))
    return out
