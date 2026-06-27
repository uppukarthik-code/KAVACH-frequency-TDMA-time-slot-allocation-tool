"""The independent verifier must REJECT every class of invalid plan."""
import copy
import allocation_solver as A


def _good():
    """A small hand-built valid problem + schedule."""
    pal = A._mk_palette()
    prob = A.Problem([1, 2], {1: 3, 2: 3}, {1: 2, 2: 2}, pal,
                     edges=[(1, 2)], f0=A.KAVACH_F0)
    colour = {1: 1, 2: 2}                       # adjacent, different pairs
    sched = {1: {'station': [2, 3, 4], 'loco': [6, 8]},
             2: {'station': [2, 3, 4], 'loco': [6, 8]}}   # co-channel-free (diff pairs)
    return prob, colour, sched


def test_good_plan_passes():
    prob, colour, sched = _good()
    assert A.validate(prob, colour, sched, im3_orders=("two",)) == []


def test_rejects_adjacent_same_colour():
    prob, colour, sched = _good()
    colour[2] = 1                                # neighbours now share a pair
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("adjacency" in e for e in errs)


def test_rejects_adjacent_loco_slots():
    prob, colour, sched = _good()
    sched[1]['loco'] = [6, 7]                     # P6,P7 are adjacent
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("adjacent loco" in e for e in errs)


def test_rejects_reserved_slot_use():
    prob, colour, sched = _good()
    sched[1]['loco'] = [1, 8]                     # P1 is reserved
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("reserved" in e for e in errs)


def test_rejects_noncontiguous_window():
    prob, colour, sched = _good()
    sched[1]['station'] = [2, 3, 10]             # not contiguous
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("not contiguous" in e for e in errs)


def test_rejects_window_loco_overlap():
    prob, colour, sched = _good()
    sched[1]['loco'] = [3, 8]                     # P3 also in the window
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("overlap" in e for e in errs)


def test_rejects_demand_mismatch():
    prob, colour, sched = _good()
    sched[1]['loco'] = [6]                        # only 1, demand is 2
    errs = A.validate(prob, colour, sched, im3_orders=("two",))
    assert any("loco-slot count" in e for e in errs)
