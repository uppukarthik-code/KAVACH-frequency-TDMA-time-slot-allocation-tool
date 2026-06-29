"""Run-provenance stamping for auditable outputs."""
import os
import provenance as PROV


def test_sha256_stable_and_nonempty(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"kavach")
    a = PROV.file_sha256(str(f))
    b = PROV.file_sha256(str(f))
    assert a == b and len(a) == 64           # deterministic, full digest


def test_sha256_missing_file_is_empty():
    assert PROV.file_sha256("/no/such/file") == ""


def test_build_includes_version_and_hash(tmp_path):
    f = tmp_path / "chart.xlsx"
    f.write_bytes(b"data")
    rows = dict(PROV.build(str(f), reuse_window=4, slot_source="slot_demand",
                           spectrum=5, validation="PASS"))
    assert rows["tool_version"] == PROV.TOOL_VERSION
    assert rows["input_file"] == "chart.xlsx"
    assert len(rows["input_sha256"]) == 64
    assert rows["reuse_window"] == 4
    assert rows["spectrum_pairs"] == 5
    assert rows["validation"] == "PASS"


def test_build_omits_optional_when_none():
    rows = dict(PROV.build("", ))
    assert "reuse_window" not in rows
    assert "generated_utc" not in rows
