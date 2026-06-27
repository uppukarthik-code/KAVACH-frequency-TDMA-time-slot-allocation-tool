"""Excel parser robustness (EH-2): duplicate IDs, missing rows, non-numeric,
empty input must all be handled explicitly, not silently."""
import warnings
import pytest

openpyxl = pytest.importorskip("openpyxl")
import excel_io  # noqa: E402


def _make_chart(tmp_path, ids, sta, loco, *, drop_sta=False, sta_override=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Frequency allocation"
    ws.cell(1, 1, "Stationary Kavach ID")
    ws.cell(2, 1, "Peak nos. of Onboard Kavach Units")
    if not drop_sta:
        ws.cell(3, 1, "Number of Stationary Kavach Tx slots")
    for k, sid in enumerate(ids):
        c = 2 + k
        ws.cell(1, c, sid)
        ws.cell(2, c, loco[k])
        if not drop_sta:
            ws.cell(3, c, (sta_override[k] if sta_override else sta[k]))
    p = tmp_path / "chart.xlsx"
    wb.save(p)
    return str(p)


def test_reads_normal_chart(tmp_path):
    p = _make_chart(tmp_path, [10001, 10002], [7, 8], [5, 6])
    ch = excel_io.read_chart(p)
    assert ch['stations'] == [10001, 10002]
    assert ch['sta_slots'] == {10001: 7, 10002: 8}
    assert ch['loco_slots'] == {10001: 5, 10002: 6}


def test_duplicate_id_raises(tmp_path):
    p = _make_chart(tmp_path, [10001, 10001], [7, 7], [5, 5])
    with pytest.raises(ValueError, match="duplicate"):
        excel_io.read_chart(p)


def test_missing_required_row_raises(tmp_path):
    p = _make_chart(tmp_path, [10001], [7], [5], drop_sta=True)
    with pytest.raises(ValueError, match="missing required row"):
        excel_io.read_chart(p)


def test_non_numeric_slot_warns_and_zeroes(tmp_path):
    p = _make_chart(tmp_path, [10001], [7], [5], sta_override=["junk"])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ch = excel_io.read_chart(p)
    assert ch['sta_slots'][10001] == 0
    assert any("non-numeric" in str(x.message) for x in w)


def test_string_typed_numeric_cells_are_parsed(tmp_path):
    # the real-world bug: slot cells stored as strings "7"
    p = _make_chart(tmp_path, [10001], [7], [5], sta_override=["7"])
    ch = excel_io.read_chart(p)
    assert ch['sta_slots'][10001] == 7


def test_no_valid_ids_raises(tmp_path):
    p = _make_chart(tmp_path, [42], [7], [5])   # 42 is outside 10000-99999
    with pytest.raises(ValueError, match="no Stationary Kavach IDs"):
        excel_io.read_chart(p)
