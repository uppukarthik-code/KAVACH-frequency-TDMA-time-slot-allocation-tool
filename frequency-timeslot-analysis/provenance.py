#!/usr/bin/env python3
"""
Run provenance for auditable allocation outputs.

Stamps each output workbook with enough metadata to tie a result back to the
exact code and inputs that produced it: tool version, the input file's SHA-256,
the slot-demand source, and the reuse window. This makes a saved allocation
self-describing for an audit ("which version, which input, which settings?").

No external dependencies and no git requirement — works inside the standalone
operator package too.
"""
from __future__ import annotations
import hashlib
import os

# Bump on any change that can alter allocation numbers. Kept here (not derived
# from git) so the standalone operator package can report it without a repo.
TOOL_VERSION = "1.1.0"


def file_sha256(path: str) -> str:
    """SHA-256 of a file, or '' if it can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def build(input_path: str, *, reuse_window=None, slot_source=None,
          spectrum=None, validation=None, timestamp=None) -> list:
    """Return provenance as a list of (key, value) rows for the output sheet.
    `timestamp` is passed in (not read here) so callers control determinism."""
    rows = [
        ("tool_version", TOOL_VERSION),
        ("input_file", os.path.basename(input_path) if input_path else ""),
        ("input_sha256", file_sha256(input_path) if input_path else ""),
    ]
    if reuse_window is not None:
        rows.append(("reuse_window", reuse_window))
    if slot_source is not None:
        rows.append(("slot_demand_source", slot_source))
    if spectrum is not None:
        rows.append(("spectrum_pairs", spectrum))
    if validation is not None:
        rows.append(("validation", validation))
    if timestamp is not None:
        rows.append(("generated_utc", timestamp))
    return rows
