"""End-to-end invariants: any plan solve_compliant returns must pass validate,
and the documented compliance clauses must not FAIL."""
import pytest
import allocation_solver as A


def _linear_problem(n=12, window=4):
    ids = list(range(1, n + 1))
    sta = {i: 6 for i in ids}
    loco = {i: 4 for i in ids}
    return A.Problem(ids, sta, loco, A._mk_palette(), reuse_window=window,
                     f0=A.KAVACH_F0)


def test_solve_compliant_output_passes_validate():
    prob = _linear_problem()
    res = A.solve_compliant(prob)
    assert res['errors'] == []
    assert res['spectrum'] >= 1


def test_compliance_report_has_no_fail():
    prob = _linear_problem()
    res = A.solve_compliant(prob)
    fails = [(c, s) for c, s in A.compliance_report(prob, res)
             if s.startswith("FAIL")]
    assert fails == []


def test_frame_offset_zero_by_default():
    prob = _linear_problem()
    res = A.solve_compliant(prob)
    offs = A.frame_offset(prob, res['schedule'], res['gap_slots'])
    assert all(v == 0 for v in offs.values())


def test_spectrum_grows_with_interference_radius():
    # path-power graph clique = window+1 -> needs window+1 colours
    s1 = A.solve_compliant(_linear_problem(window=1))['spectrum']
    s2 = A.solve_compliant(_linear_problem(window=2))['spectrum']
    assert s1 == 2 and s2 == 3


def test_empty_problem_raises_clearly():
    prob = A.Problem([], {}, {}, A._mk_palette(), f0=A.KAVACH_F0)
    with pytest.raises(ValueError, match="no stations"):
        A.solve_compliant(prob)


def test_empty_palette_raises_clearly():
    prob = A.Problem([1], {1: 2}, {1: 2}, [], f0=A.KAVACH_F0)
    with pytest.raises(ValueError, match="empty frequency palette"):
        A.solve_compliant(prob)


def test_frame_offset_logic():
    prob = A.Problem([1], {1: 4}, {1: 3}, A._mk_palette(), f0=A.KAVACH_F0)
    gap = A.default_gap_slots()
    # loco well before window -> offset 0
    s0 = {1: {'loco': [2, 4, 6], 'station': list(range(2 + 6 + gap,
                                                       2 + 6 + gap + 4))}}
    assert A.frame_offset(prob, s0, gap)[1] == 0
    # loco after window -> offset 1
    s1 = {1: {'station': [2, 3, 4, 5], 'loco': [7, 9, 11]}}
    assert A.frame_offset(prob, s1, gap)[1] == 1
