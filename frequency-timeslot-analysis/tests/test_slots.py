"""Slot-placement completeness (audit D1), input-size guards (B3) and
reproducibility (B7)."""
import itertools
import pytest
import allocation_solver as A
from allocation_solver import _place_compact, _place_offset0


# ---------------------------------------------------------------------------
# Independent brute-force oracle: does ANY valid (contiguous window + n_loco
# non-adjacent) layout exist in `free`?  Deliberately structured differently
# from the placer (enumerate windows, then enumerate loco subsets) so it is a
# genuine cross-check, not a restatement of the implementation.
# ---------------------------------------------------------------------------
def _layout_exists(free, n_sta, n_loco, nonadj=True):
    free = sorted(free)
    if n_sta == 0:
        windows = [[]]
    else:
        windows = [free[i:i + n_sta] for i in range(len(free) - n_sta + 1)
                   if free[i + n_sta - 1] - free[i] == n_sta - 1]
    for w in windows:
        rest = [t for t in free if t not in set(w)]
        for combo in itertools.combinations(rest, n_loco):
            if not nonadj or all(b - a >= 2 for a, b in zip(combo, combo[1:])):
                return True
    return False


@pytest.mark.parametrize("size", range(1, 8))
def test_place_compact_is_complete(size):
    """_place_compact must succeed exactly when a valid layout exists, and the
    layout it returns must satisfy every placement rule (audit D1)."""
    free = list(range(1, size + 1))
    for n_sta in range(0, size + 1):
        for n_loco in range(0, size + 1):
            got = _place_compact(free, n_sta, n_loco, True)
            exp = _layout_exists(free, n_sta, n_loco, True)
            assert (got is not None) == exp, (free, n_sta, n_loco, got, exp)
            if got is not None:
                win, loco = sorted(got['station']), sorted(got['loco'])
                assert len(win) == n_sta and len(loco) == n_loco
                assert not (set(win) & set(loco))
                if win:
                    assert win[-1] - win[0] == len(win) - 1      # contiguous
                assert all(b - a >= 2 for a, b in zip(loco, loco[1:]))  # non-adj
                assert set(win + loco) <= set(free)


def test_place_compact_d1_counterexample():
    """The exact case the audit flagged: earliest window [1] strands the loco
    slots, but window [2] leaves [1,3,5]. Must NOT spuriously fail."""
    got = _place_compact([1, 2, 3, 4, 5], n_sta=1, n_loco=3, nonadj=True)
    assert got is not None
    assert len(got['station']) == 1 and len(got['loco']) == 3


def test_place_offset0_still_complete_on_contiguous_frames():
    """Regression guard for the offset-0 placer (already complete)."""
    free = list(range(1, 9))
    for n_sta in range(0, 5):
        for n_loco in range(0, 4):
            got = _place_offset0(free, n_sta, n_loco, True, gap=2)
            if got is not None:
                win, loco = sorted(got['station']), sorted(got['loco'])
                assert len(win) == n_sta and len(loco) == n_loco
                if win and loco:
                    assert min(win) - max(loco) >= 2          # offset-0 gap


def test_pipeline_compact_strategy_is_valid():
    ids = list(range(1, 9))
    prob = A.Problem(ids, {i: 3 for i in ids}, {i: 2 for i in ids},
                     A._mk_palette(), reuse_window=3, f0=A.KAVACH_F0)
    res = A.solve(prob, slot_strategy="compact")
    assert res['errors'] == []


# ---------------------------------------------------------------------------
# Input-size guard (audit B3)
# ---------------------------------------------------------------------------
def test_oversized_station_count_rejected():
    from model import MAX_STATIONS
    n = MAX_STATIONS + 1
    ids = list(range(n))
    with pytest.raises(ValueError, match="exceeds the single-section"):
        A.Problem(ids, {i: 1 for i in ids}, {i: 1 for i in ids},
                  A._mk_palette(), f0=A.KAVACH_F0)


def test_oversized_palette_rejected():
    from model import MAX_PALETTE, Pair
    pal = [Pair(i, 400.0 + i * 0.025, 410.0 + i * 0.025)
           for i in range(MAX_PALETTE + 1)]
    with pytest.raises(ValueError, match="exceeds"):
        A.Problem([1], {1: 1}, {1: 1}, pal, f0=A.KAVACH_F0)


# ---------------------------------------------------------------------------
# Reproducibility (audit B7): same input -> identical allocation
# ---------------------------------------------------------------------------
def test_solve_compliant_is_reproducible():
    pytest.importorskip("ortools")
    ids = list(range(1, 13))

    def mk():
        return A.Problem(ids, {i: 6 for i in ids}, {i: 4 for i in ids},
                         A._mk_palette(), reuse_window=4, f0=A.KAVACH_F0)
    r1 = A.solve_compliant(mk())
    r2 = A.solve_compliant(mk())
    assert r1['colour'] == r2['colour']
    assert r1['schedule'] == r2['schedule']
    assert r1['used_pairs'] == r2['used_pairs']


# ---------------------------------------------------------------------------
# IM3 enumeration memoisation (audit A4): caching must not change results
# ---------------------------------------------------------------------------
def test_im3_memoisation_is_consistent():
    pal = A._mk_palette()
    a = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, orders=("two", "three"))
    b = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, orders=("two", "three"))
    assert a == b                       # equal value across calls
    assert a is not b                   # but a fresh list each time (safe to own)
    a.append("scribble")
    c = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, orders=("two", "three"))
    assert "scribble" not in c          # mutating one result never leaks
