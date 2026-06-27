"""IM3 engine + Sidon construction (interference-correctness core)."""
import allocation_solver as A


def test_reference_palette_two_tone_triples():
    """The reference palette's two-tone forbidden colour-sets are the known
    triples (1,4,7),(2,3,7),(2,6,7) — golden values."""
    pal = A._mk_palette()
    forb = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, include_f0=False,
                                       orders=("two",))
    got = {tuple(sorted(k)) for k in forb}
    assert got == {(1, 4, 7), (2, 3, 7), (2, 6, 7)}


def test_reference_palette_has_three_tone_residuals():
    pal = A._mk_palette()
    full = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, include_f0=False)
    # full set is a strict superset of the two-tone set
    two = A.im3_forbidden_colour_sets(pal, A.KAVACH_F0, include_f0=False,
                                      orders=("two",))
    assert len(full) > len(two)


def test_palette_not_fully_clean_but_two_tone_subsets_exist():
    pal = A._mk_palette()
    clean, _ = A.palette_is_im3_clean(pal, A.KAVACH_F0, include_f0=False)
    assert clean is False


def test_design_im3_free_palette_is_sidon_and_clean():
    fresh = A.design_im3_free_palette(5, 406.0, 470.0)
    comb = [A._chan(p.fS) for p in fresh] + [A._chan(p.fM) for p in fresh]
    assert A.is_sidon(comb) is True
    clean, fs = A.palette_is_im3_clean(fresh)
    assert clean is True and fs == []


def test_is_sidon_basic():
    assert A.is_sidon([1, 2, 5, 11]) is True       # classic Sidon set
    assert A.is_sidon([1, 2, 3, 4]) is False        # 1+4 == 2+3


def test_default_gap_slots_matches_spec_band():
    # 150-200 ms / 27.5 ms marker spacing -> 6-8 markers; default (200 ms) = 8
    assert A.default_gap_slots(150.0) == 6
    assert A.default_gap_slots(200.0) == 8
