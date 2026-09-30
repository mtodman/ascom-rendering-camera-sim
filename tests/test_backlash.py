import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focuser_sim.backlash import IN, OUT, BacklashModel


def test_no_backlash_tracks_motor():
    m = BacklashModel(1000, 0, 0)
    m.move(50)
    m.move(-80)
    assert m.motor == 970
    assert m.optical == 970


def test_same_direction_moves_one_for_one():
    m = BacklashModel(1000, 100, 100, engaged=OUT)
    m.move(250)
    assert (m.motor, m.optical) == (1250, 1250)


def test_reversal_absorbs_backlash():
    m = BacklashModel(1000, backlash_in_steps=100, backlash_out_steps=100, engaged=OUT)
    m.move(-60)  # reversing IN: all absorbed
    assert (m.motor, m.optical) == (940, 1000)
    m.move(-100)  # 40 more absorbed, then 60 real
    assert (m.motor, m.optical) == (840, 940)
    assert m.engaged == IN


def test_partial_takeup_is_undone_without_moving_drawtube():
    m = BacklashModel(1000, 100, 100, engaged=OUT)
    m.move(-30)
    m.move(50)  # 30 undo the take-up, 20 move the drawtube out
    assert (m.motor, m.optical) == (1020, 1020)
    assert m.engaged == OUT
    assert m.takeup == 0


def test_asymmetric_backlash():
    m = BacklashModel(1000, backlash_in_steps=40, backlash_out_steps=150, engaged=OUT)
    m.move(-100)  # reversing IN loses 40
    assert m.optical == 940
    m.move(200)  # reversing OUT loses 150
    assert (m.motor, m.optical) == (1100, 990)


def test_classic_autofocus_offset():
    """Moving out past focus then back in to it lands the drawtube short
    by exactly the IN backlash - the symptom this simulator exists for."""
    m = BacklashModel(25000, 100, 100, engaged=OUT)
    m.move(500)
    m.move(-500)
    assert m.motor == 25000
    assert m.optical == 25100


def test_reducing_backlash_below_takeup_engages_reverse():
    m = BacklashModel(1000, 100, 100, engaged=OUT)
    m.move(-60)
    m.set_backlash(50, 100)
    assert m.engaged == IN
    m.move(-10)
    assert m.optical == 990


def test_reducing_backlash_to_zero_when_fully_engaged_keeps_direction():
    m = BacklashModel(1000, 100, 100, engaged=OUT)
    m.set_backlash(0, 100)
    assert m.engaged == OUT
    m.move(10)
    assert m.optical == 1010


def test_reset_resyncs_optical():
    m = BacklashModel(1000, 100, 100, engaged=OUT)
    m.move(-60)
    m.reset(engaged=IN)
    assert (m.motor, m.optical, m.takeup, m.engaged) == (940, 940, 0, IN)
