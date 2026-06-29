#!/usr/bin/env python3
"""
Boundary-frequency handling for per-section KAVACH allocation.

A section is allocated on its own (e.g. SEC-A(excl)-SEC-B(excl)). The first and last
stations abut the NEIGHBOURING sections, so to keep frequencies consistent at the
join, those boundary stations are PINNED to specific frequency pairs (the ones
the adjacent section already uses) instead of being chosen freely.

Pins are obtained, in order of preference:
  1. a boundary file   (scalable, auditable, version-controlled) -- read_boundary_file()
  2. an interactive prompt (one-off use; only when stdin is a TTY) -- prompt_boundary()
  3. none              (tool chooses freely)

`apply_pins()` turns the pins into `Problem.allowed` constraints, so the existing
colourer simply respects them. This is the lightweight alternative to a national
"decomposition orchestrator": coordinate only the 1-2 boundary stations, not the
whole network. See module docstring of slot_demand for the scalable registry idea.
"""
from __future__ import annotations
import csv
import sys


def boundary_stations(ordered_ids: list) -> list:
    """The stations that touch the neighbouring sections = first and last in
    geographic order. (Single-station sections: just that one.)"""
    ids = list(ordered_ids)
    if len(ids) <= 1:
        return ids
    return [ids[0], ids[-1]]


def read_boundary_file(path: str) -> dict:
    """Read pinned boundary pairs from a CSV with columns `station_id,pair_id`
    (header required). Returns {station_id: pair_id}. This is the SCALABLE,
    auditable way to supply boundary frequencies (one file per section, kept
    under version control / in a zone registry)."""
    pins = {}
    with open(path, newline="") as fh:
        rdr = csv.DictReader(fh)
        cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
        sid_c = cols.get("station_id") or cols.get("station") or cols.get("id")
        pid_c = cols.get("pair_id") or cols.get("pair") or cols.get("frequency_pair")
        if not sid_c or not pid_c:
            raise ValueError("boundary file needs columns 'station_id' and 'pair_id'")
        for row in rdr:
            s, p = row.get(sid_c), row.get(pid_c)
            if s in (None, "") or p in (None, ""):
                continue
            try:
                pins[int(float(s))] = int(float(p))
            except ValueError:
                pins[str(s).strip()] = int(float(p))
    return pins


def write_boundary_template(path: str, ordered_ids: list) -> str:
    """Write a starter boundary file for a section's boundary stations, so an
    engineer can fill in the pair indices once and re-use it."""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station_id", "pair_id"])
        for sid in boundary_stations(ordered_ids):
            w.writerow([sid, ""])
    return path


def prompt_boundary(boundary_ids: list, palette: list) -> dict:
    """Interactively ASK the engineer for each boundary station's pinned pair
    (do not assume). Only call when stdin is a TTY. Blank = let the tool choose."""
    valid = sorted(p.id for p in palette)
    pins = {}
    print("\nBoundary frequency pinning — keep adjacent sections consistent.")
    print(f"  Available pair indices: {valid}")
    for sid in boundary_ids:
        while True:
            ans = input(f"  Station {sid}: pair index used by the ADJACENT "
                        f"section (blank = tool chooses): ").strip()
            if ans == "":
                break
            try:
                p = int(ans)
            except ValueError:
                print(f"    not a number; enter one of {valid} or blank")
                continue
            if p in valid:
                pins[sid] = p
                break
            print(f"    {p} is not in the palette {valid}")
    return pins


def resolve_pins(ordered_ids: list, palette: list, *, boundary_file: str = None,
                 interactive: bool = None) -> dict:
    """Get boundary pins: file if given, else prompt if interactive (TTY by
    default), else none. Returns {station_id: pair_id}."""
    if boundary_file:
        return read_boundary_file(boundary_file)
    if interactive is None:
        interactive = sys.stdin.isatty()
    if interactive:
        return prompt_boundary(boundary_stations(ordered_ids), palette)
    return {}


def apply_pins(problem, pins: dict):
    """Constrain each pinned station to its single allowed pair, validating that
    the pair exists in the palette. The existing colourer then respects it."""
    valid = {p.id for p in problem.palette}
    for sid, pid in pins.items():
        if pid not in valid:
            raise ValueError(f"boundary pin for {sid}: pair {pid} not in palette {sorted(valid)}")
        if sid not in problem.stations:
            continue  # pin for a station not in this section; ignore
        problem.allowed[sid] = {pid}
    return problem


# ===========================================================================
# (a) RESERVED BOUNDARY SUB-PALETTE
#   Keep a small set of pairs for boundary stations ONLY, so most boundaries
#   need zero cross-section negotiation: interior stations never use a reserved
#   pair, so a neighbouring section's boundary can't clash with this section's
#   interior. (Costs a little spectrum; needs palette headroom for the interior.)
# ===========================================================================
def reserved_pairs(palette: list, n: int) -> set:
    """The `n` highest-id pairs of the palette, reserved for boundary use."""
    return set(sorted((p.id for p in palette), reverse=True)[:max(0, n)])


def reserve_for_boundary(problem, reserved_ids, boundary_ids=None):
    """Restrict interior stations to NON-reserved pairs and (unpinned) boundary
    stations to the reserved pairs. Apply this BEFORE specific pins, so a
    registry/file pin can still fix a boundary station to one reserved pair."""
    reserved = set(reserved_ids)
    allp = {p.id for p in problem.palette}
    interior_allowed = allp - reserved
    if not interior_allowed:
        raise ValueError("reserving every pair leaves none for interior stations")
    bset = set(boundary_ids) if boundary_ids is not None \
        else set(boundary_stations(problem.stations))
    for s in problem.stations:
        problem.allowed[s] = set(reserved) if s in bset else set(interior_allowed)
    return problem


# ===========================================================================
# (b) NATIONAL BOUNDARY REGISTRY (single source of truth, versioned CSV)
#   Run sections in order; each reads the registry to pin any of its stations a
#   neighbour already fixed, then writes its own boundary assignments back.
#   O(boundaries) data -- no database, no global solve.
# ===========================================================================
def read_registry(path: str) -> dict:
    """{station_id: pair_id} from the registry CSV; {} if the file doesn't exist."""
    try:
        return read_boundary_file(path)
    except FileNotFoundError:
        return {}


def registry_pins_for(registry: dict, ordered_ids: list) -> dict:
    """Pins for THIS section's stations that a neighbour already fixed."""
    return {sid: registry[sid] for sid in ordered_ids if sid in registry}


def update_registry(path: str, ordered_ids: list, colour: dict) -> dict:
    """Merge this section's boundary-station -> pair assignments into the
    registry file (so the next section reads them). Returns the merged registry."""
    reg = read_registry(path)
    for sid in boundary_stations(ordered_ids):
        if sid in colour:
            reg[sid] = colour[sid]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station_id", "pair_id"])
        for sid, pid in sorted(reg.items(), key=lambda kv: str(kv[0])):
            w.writerow([sid, pid])
    return reg
