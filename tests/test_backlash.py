import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from focuser_sim.backlash import IN, OUT, BacklashModel


def test_no_backlash_tracks_motor():
    m = BacklashModel(1000, 0)
    m.move(50)
    m.move(-80)
    assert m.motor == 970
    assert m.optical == 970


def test_same_direction_moves_one_for_one():
    m = BacklashModel(1000, 100, engaged=OUT)
    m.move(250)
    assert (m.motor, m.optical) == (1250, 1250)


def test_reversal_absorbs_backlash():
    m = BacklashModel(1000, backlash_steps=100, engaged=OUT)
    m.move(-60)  # reversing IN: all absorbed
    assert (m.motor, m.optical) == (940, 1000)
    m.move(-100)  # 40 more absorbed, then 60 real
    assert (m.motor, m.optical) == (840, 940)
    assert m.engaged == IN


def test_partial_takeup_is_undone_without_moving_drawtube():
    m = BacklashModel(1000, 100, engaged=OUT)
    m.move(-30)
    m.move(50)  # 30 undo the take-up, 20 move the drawtube out
    assert (m.motor, m.optical) == (1020, 1020)
    assert m.engaged == OUT
    assert m.takeup == 0


def test_repeated_reversals_never_drift():
    """Regression: with the old separate IN/OUT amounts (e.g. IN 0 / OUT 100),
    each out-and-back cycle ratcheted the drawtube 100 steps further from the
    motor, without bound - an autofocus run's many reversals left it
    thousands of steps out. One physical gap can't do that: however many
    reversals, |Position - optical| stays within the gap."""
    m = BacklashModel(25000, 100, engaged=OUT)
    for _ in range(50):
        m.move(+240)
        m.move(-120)
        m.move(-120)
        assert abs(m.motor - m.optical) <= 100
        assert m.motor - m.optical == -100  # engaged IN: offset is exactly the gap
    m.move(+500)  # engaged OUT again: back to exactly the starting offset
    assert m.motor - m.optical == 0


def test_reversal_loses_same_amount_both_ways():
    m = BacklashModel(1000, 70, engaged=OUT)
    m.move(-200)
    assert m.optical == 1000 - 130
    m.move(+200)
    assert m.optical == 1000 - 130 + 130


def test_classic_autofocus_offset():
    """Moving out past focus then back in to it lands the drawtube short
    by exactly the IN backlash - the symptom this simulator exists for."""
    m = BacklashModel(25000, 100, engaged=OUT)
    m.move(500)
    m.move(-500)
    assert m.motor == 25000
    assert m.optical == 25100


def test_reducing_backlash_below_takeup_engages_reverse():
    m = BacklashModel(1000, 100, engaged=OUT)
    m.move(-60)
    m.set_backlash(50)
    assert m.engaged == IN
    m.move(-10)
    assert m.optical == 990


def test_reducing_backlash_to_zero_when_fully_engaged_keeps_direction():
    m = BacklashModel(1000, 100, engaged=OUT)
    m.set_backlash(0)
    assert m.engaged == OUT
    m.move(10)
    assert m.optical == 1010


def test_reset_resyncs_optical():
    m = BacklashModel(1000, 100, engaged=OUT)
    m.move(-60)
    m.reset(engaged=IN)
    assert (m.motor, m.optical, m.takeup, m.engaged) == (940, 940, 0, IN)


def test_legacy_in_out_config_migrates_to_single_gap(tmp_path):
    from ruamel.yaml import YAML

    from focuser_sim.config import FocuserSimConfig, load_settings_with_raw

    assert FocuserSimConfig(backlash_in_steps=0, backlash_out_steps=100).backlash_steps == 100
    assert FocuserSimConfig(backlash_in_steps=40, backlash_out_steps=10).backlash_steps == 40
    assert FocuserSimConfig(backlash_steps=7, backlash_in_steps=99).backlash_steps == 7

    cfg_path = tmp_path / "focuser_sim.yaml"
    cfg_path.write_text("focuser:\n  backlash_in_steps: 0\n  backlash_out_steps: 100\n  start_position: 123\n")
    settings, raw = load_settings_with_raw(cfg_path)
    assert settings.focuser.backlash_steps == 100
    settings.save(cfg_path, raw)
    saved = YAML().load(cfg_path.read_text())["focuser"]
    assert saved["backlash_steps"] == 100
    assert "backlash_in_steps" not in saved and "backlash_out_steps" not in saved
    assert saved["start_position"] == 123
