"""Time-domain staggering must never increase IM coincidence and should reach 0
on a linear section. count_im_coincidence is an independent counter."""
import pytest
import allocation_solver as A

pytest.importorskip("ortools")
import stagger as S  # noqa: E402


def _linear(n=12, window=4):
    ids = list(range(1, n + 1))
    return A.Problem(ids, {i: 6 for i in ids}, {i: 4 for i in ids},
                     A._mk_palette(), reuse_window=window, f0=A.KAVACH_F0)


def test_staggering_does_not_increase_coincidence():
    prob = _linear()
    res = A.solve_compliant(prob, time_stagger=False)   # plain offset-0
    before, _ = S.count_im_coincidence(prob, res['colour'], res['schedule'], "full")
    sched2 = S.stagger_slots(prob, res['colour'], im_level="full")
    after, _ = S.count_im_coincidence(prob, res['colour'], sched2, "full")
    assert after <= before


def test_default_pipeline_reports_zero_coincidence():
    prob = _linear()
    res = A.solve_compliant(prob)            # staggering on by default
    assert res['staggered'] is True
    assert res['im_coincidence'] is not None
    assert res['im_coincidence'][0] == 0


def test_staggered_schedule_still_valid():
    prob = _linear()
    res = A.solve_compliant(prob)
    assert res['errors'] == []


def test_staggered_flag_false_when_staggering_fails(monkeypatch):
    """EH-1 regression: if stagger_slots raises, the pipeline must fall back,
    warn, and report staggered=False (not a misleading True)."""
    import warnings
    prob = _linear()

    def boom(*a, **k):
        raise ValueError("stagger: no feasible slot placement")
    monkeypatch.setattr(S, "stagger_slots", boom)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        res = A.solve_compliant(prob)            # time_stagger on by default
    assert res['staggered'] is False
    assert res['errors'] == []                   # fallback offset-0 still valid
    assert any("staggering failed" in str(x.message) for x in w)
