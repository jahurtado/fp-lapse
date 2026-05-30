"""Tests for the datetime digit picker (prd2.md §6.3)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date as date_t, time as time_t
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402
from fp_lapse.ui.picker_datetime import (  # noqa: E402
    DateTimePickerInteraction,
    PickerAction,
    PickerMode,
    render_datetime_picker,
)
from fp_lapse.ui.picker_validate import validate_time_digits  # noqa: E402


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"


def _blank_base() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), (10, 14, 20))


class TestValidateTimeDigits(unittest.TestCase):
    def test_valid_time_only(self):
        r = validate_time_digits(
            year=None, month=None, day=None,
            hour=12, minute=30, second=45, mode="time",
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.time, time_t(12, 30, 45))
        self.assertIsNone(r.date)

    def test_valid_date_time(self):
        r = validate_time_digits(
            year=2026, month=8, day=12,
            hour=11, minute=33, second=23, mode="date_time",
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.date, date_t(2026, 8, 12))
        self.assertEqual(r.time, time_t(11, 33, 23))

    def test_rejects_year_below_2000(self):
        r = validate_time_digits(
            year=1999, month=1, day=1, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_rejects_month_zero(self):
        r = validate_time_digits(
            year=2026, month=0, day=1, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_rejects_month_above_12(self):
        r = validate_time_digits(
            year=2026, month=13, day=1, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_rejects_day_zero(self):
        r = validate_time_digits(
            year=2026, month=1, day=0, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_rejects_day_above_month_length(self):
        r = validate_time_digits(
            year=2026, month=4, day=31, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_rejects_feb_29_on_non_leap(self):
        # 2025 is not a leap year.
        r = validate_time_digits(
            year=2025, month=2, day=29, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertFalse(r.ok)

    def test_accepts_feb_29_on_leap(self):
        r = validate_time_digits(
            year=2024, month=2, day=29, hour=0, minute=0, second=0,
            mode="date_time",
        )
        self.assertTrue(r.ok)

    def test_rejects_hour_above_23(self):
        r = validate_time_digits(
            year=None, month=None, day=None,
            hour=24, minute=0, second=0, mode="time",
        )
        self.assertFalse(r.ok)

    def test_rejects_minute_above_59(self):
        r = validate_time_digits(
            year=None, month=None, day=None,
            hour=0, minute=60, second=0, mode="time",
        )
        self.assertFalse(r.ok)

    def test_rejects_second_above_59(self):
        r = validate_time_digits(
            year=None, month=None, day=None,
            hour=0, minute=0, second=60, mode="time",
        )
        self.assertFalse(r.ok)


class TestPickerInteractionBasics(unittest.TestCase):
    def test_constructed_with_target_field(self):
        p = DateTimePickerInteraction(target_field="start")
        self.assertEqual(p.target_field, "start")

    def test_system_clock_forces_date_time_mode(self):
        # Even if no initial value is provided, system_clock forces
        # DATE_TIME (the operator must enter date + time).
        p = DateTimePickerInteraction(target_field="system_clock")
        self.assertEqual(p.mode, PickerMode.DATE_TIME)

    def test_start_with_date_value_picks_date_time_mode(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(
                time=time_t(11, 0, 0), date=date_t(2026, 8, 12),
            ),
        )
        self.assertEqual(p.mode, PickerMode.DATE_TIME)

    def test_start_with_time_only_picks_time_mode(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(time=time_t(9, 0, 0), date=None),
        )
        self.assertEqual(p.mode, PickerMode.TIME)


class TestPickerCursorMovement(unittest.TestCase):
    def test_right_advances_left_retreats(self):
        p = DateTimePickerInteraction(target_field="system_clock")
        self.assertEqual(p.state.cursor, 0)
        p.on_press(ButtonId.RIGHT)
        self.assertEqual(p.state.cursor, 1)
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, 0)

    def test_left_clamps_at_zero(self):
        p = DateTimePickerInteraction(target_field="system_clock")
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, 0)

    def test_right_clamps_at_last(self):
        p = DateTimePickerInteraction(target_field="system_clock")
        for _ in range(50):
            p.on_press(ButtonId.RIGHT)
        self.assertEqual(p.state.cursor, len(p.state.digits) - 1)


class TestPickerDigitCycling(unittest.TestCase):
    def test_up_increments_with_wrap(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(time=time_t(9, 0, 9), date=None),
        )
        # Move to last digit (seconds units, value 9) and bump up.
        for _ in range(len(p.state.digits) - 1):
            p.on_press(ButtonId.RIGHT)
        self.assertEqual(p.state.digits[-1], 9)
        p.on_press(ButtonId.UP)
        self.assertEqual(p.state.digits[-1], 0)  # 9 → 0 wrap

    def test_down_decrements_with_wrap(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(time=time_t(0, 0, 0), date=None),
        )
        p.on_press(ButtonId.DOWN)
        # First digit was 0 (tens-of-hour, max 5) → wraps to 5.
        self.assertEqual(p.state.digits[0], 5)


class TestPickerCommit(unittest.TestCase):
    def test_ok_with_valid_returns_save(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(
                time=time_t(11, 0, 0), date=date_t(2026, 8, 12),
            ),
        )
        r = p.on_press(ButtonId.OK)
        self.assertEqual(r, PickerAction.SAVE)

    def test_commit_returns_moment(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
        )
        m = p.commit()
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.date, date_t(2026, 8, 12))
        self.assertEqual(m.time, time_t(11, 33, 23))

    def test_back_returns_cancel(self):
        p = DateTimePickerInteraction(target_field="start")
        self.assertEqual(p.on_press(ButtonId.BACK), PickerAction.CANCEL)

    def test_ok_with_invalid_returns_none_and_sets_error(self):
        # Set day to 00 (always invalid) by overwriting both day digits.
        p = DateTimePickerInteraction(target_field="system_clock")
        # Cells layout (DATE_TIME): [Y0,Y1,Y2,Y3, M0,M1, D0,D1, …]
        # cell index 6 = D0 (day tens), 7 = D1 (day units).
        # Drive the cursor to cell 6, then zero D0 (cycle DOWN at
        # cursor=6 until 0), then move to 7 and zero D1.
        for _ in range(6):
            p.on_press(ButtonId.RIGHT)
        # cursor now at 6 (D0). max_value = 3 so at most 4 iterations.
        for _ in range(4):
            if p.state.digits[6] == 0:
                break
            p.on_press(ButtonId.DOWN)
        self.assertEqual(p.state.digits[6], 0)
        p.on_press(ButtonId.RIGHT)
        # cursor now at 7 (D1). max_value = 9 so at most 10 iterations.
        for _ in range(10):
            if p.state.digits[7] == 0:
                break
            p.on_press(ButtonId.DOWN)
        self.assertEqual(p.state.digits[7], 0)
        # Now day == 00; OK should fail validation.
        r = p.on_press(ButtonId.OK)
        self.assertIsNone(r)
        self.assertIsNotNone(p.state.error)


class TestPickerRendering(unittest.TestCase):
    def test_render_time_mode_smoke(self):
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(time=time_t(9, 0, 0), date=None),
        )
        out = render_datetime_picker(_blank_base(), p.state, title="test")
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_render_date_time_mode_smoke(self):
        p = DateTimePickerInteraction(target_field="system_clock")
        out = render_datetime_picker(_blank_base(), p.state, title="Set clock")
        self.assertEqual(out.size, (WIDTH, HEIGHT))

    def test_render_with_error_includes_error_line(self):
        p = DateTimePickerInteraction(target_field="system_clock")
        # Drive day cells to 00 (invalid) via the same path as the
        # earlier commit test, then OK to force an error.
        for _ in range(6):
            p.on_press(ButtonId.RIGHT)
        for _ in range(4):
            if p.state.digits[6] == 0:
                break
            p.on_press(ButtonId.DOWN)
        p.on_press(ButtonId.RIGHT)
        for _ in range(10):
            if p.state.digits[7] == 0:
                break
            p.on_press(ButtonId.DOWN)
        p.on_press(ButtonId.OK)
        self.assertIsNotNone(p.state.error)
        out_no_err = render_datetime_picker(
            _blank_base(),
            # Build a state without the error.
            p.state.__class__(
                digits=p.state.digits, cursor=p.state.cursor,
                mode=p.state.mode, error=None,
            ),
            title="t",
        )
        out_with_err = render_datetime_picker(
            _blank_base(), p.state, title="t",
        )
        self.assertNotEqual(out_no_err.tobytes(), out_with_err.tobytes())


class TestPickerVisualRegression(unittest.TestCase):
    def test_12_picker_datetime(self):
        # Replicates render_picker_datetime() in
        # docs/mockups/render_mockups.py.
        from fp_lapse.configs import Shot, TimelapseConfig
        from fp_lapse.ui.edit_screen import EditScreen, EditState

        cfg = TimelapseConfig(
            name="Totality", interval_s=5.0,
            shots=(
                Shot(shutter=1 / 500, iso=400, aperture=None),
                Shot(shutter=1 / 125, iso=400, aperture=None),
                Shot(shutter=1 / 30, iso=400, aperture=None),
                Shot(shutter=1 / 8, iso=400, aperture=None),
                Shot(shutter=2.0, iso=1600, aperture=None),
            ),
            start=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
            end=ScheduledMoment(
                time=time_t(11, 36, 9), date=date_t(2026, 8, 12),
            ),
        )
        base = EditScreen().render(
            EditState(cfg=cfg, field_cursor=3, scroll_offset=0)
        )
        picker = DateTimePickerInteraction(
            target_field="start",
            initial_value=cfg.start,
        )
        for _ in range(10):
            picker.on_press(ButtonId.RIGHT)
        actual = render_datetime_picker(
            base, picker.state, title="Edit · Totality · start",
        )
        expected_path = MOCKUPS_DIR / "12_picker_datetime.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        self.assertEqual(actual.tobytes(), expected.tobytes())


if __name__ == "__main__":
    unittest.main()


class TestPickerModeChip(unittest.TestCase):
    """Addendum F: mode chip on the picker — cycle through NONE / TIME
    / DATE_TIME, clear the field via NONE, navigate between chip and
    digits with LEFT/RIGHT."""

    def test_none_field_picker_starts_on_chip_in_none_mode(self):
        """Opening the picker on an unset START/END lands on the chip
        with mode = NONE — operator must explicitly enter a mode."""
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        self.assertEqual(p.mode, PickerMode.NONE)
        # Cursor sentinel -1 means "chip".
        self.assertEqual(p.state.cursor, -1)

    def test_set_field_picker_starts_on_first_digit(self):
        """A picker opened on an already-set moment lands on the first
        digit so the operator can type immediately. LEFT one step
        reaches the chip when they need to switch modes."""
        p = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(time=time_t(9, 0, 0)),
        )
        self.assertEqual(p.state.cursor, 0)
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, -1)  # landed on chip

    def test_up_on_chip_cycles_mode_forward(self):
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        self.assertEqual(p.mode, PickerMode.NONE)
        p.on_press(ButtonId.UP)
        self.assertEqual(p.mode, PickerMode.TIME)
        p.on_press(ButtonId.UP)
        self.assertEqual(p.mode, PickerMode.DATE_TIME)
        p.on_press(ButtonId.UP)
        self.assertEqual(p.mode, PickerMode.NONE)  # wraps

    def test_down_on_chip_cycles_mode_backward(self):
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        p.on_press(ButtonId.DOWN)
        self.assertEqual(p.mode, PickerMode.DATE_TIME)
        p.on_press(ButtonId.DOWN)
        self.assertEqual(p.mode, PickerMode.TIME)
        p.on_press(ButtonId.DOWN)
        self.assertEqual(p.mode, PickerMode.NONE)

    def test_right_from_chip_moves_to_first_digit(self):
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        # Switch to TIME so digits exist.
        p.on_press(ButtonId.UP)
        self.assertEqual(p.mode, PickerMode.TIME)
        self.assertEqual(p.state.cursor, -1)  # still on chip
        p.on_press(ButtonId.RIGHT)
        self.assertEqual(p.state.cursor, 0)   # first digit

    def test_right_from_chip_in_none_mode_stays_on_chip(self):
        """NONE mode has no digits — RIGHT from chip has nowhere to
        go and is a no-op."""
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        self.assertEqual(p.mode, PickerMode.NONE)
        p.on_press(ButtonId.RIGHT)
        self.assertEqual(p.state.cursor, -1)

    def test_left_from_first_digit_lands_on_chip(self):
        p = DateTimePickerInteraction(
            target_field="end",
            initial_value=ScheduledMoment(
                time=time_t(11, 36, 9), date=date_t(2026, 8, 12),
            ),
        )
        self.assertEqual(p.state.cursor, 0)
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, -1)
        # Pressing LEFT again is a no-op (already leftmost).
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, -1)

    def test_is_clear_request_true_only_for_none_mode(self):
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        self.assertTrue(p.is_clear_request)
        p.on_press(ButtonId.UP)  # → TIME
        self.assertFalse(p.is_clear_request)
        p.on_press(ButtonId.UP)  # → DATE_TIME
        self.assertFalse(p.is_clear_request)
        p.on_press(ButtonId.UP)  # wrap → NONE
        self.assertTrue(p.is_clear_request)

    def test_ok_in_none_mode_returns_save_without_validation(self):
        """NONE has no digits to validate — OK saves cleanly."""
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        self.assertEqual(p.on_press(ButtonId.OK), PickerAction.SAVE)
        # And commit() returns None (no moment); App reads
        # is_clear_request to disambiguate from a validation error.
        self.assertIsNone(p.commit())
        self.assertTrue(p.is_clear_request)

    def test_system_clock_chip_is_hidden(self):
        """system_clock locks mode to DATE_TIME; chip is hidden and
        the cursor cannot reach -1."""
        p = DateTimePickerInteraction(target_field="system_clock")
        self.assertFalse(p.state.show_mode_chip)
        # LEFT from cursor=0 must NOT go to -1.
        p.on_press(ButtonId.LEFT)
        self.assertEqual(p.state.cursor, 0)

    def test_mode_change_preserves_typed_digits_per_mode(self):
        """Cycling through modes shouldn't lose what the operator
        typed in each mode."""
        p = DateTimePickerInteraction(target_field="start", initial_value=None)
        # NONE → TIME → type something → NONE → TIME → digits restored
        p.on_press(ButtonId.UP)  # → TIME
        p.on_press(ButtonId.RIGHT)  # land on first digit
        p.on_press(ButtonId.UP)  # bump tens-of-hour 0→1
        self.assertEqual(p.state.digits[0], 1)
        # Cursor back to chip + cycle to DATE_TIME + back to TIME
        p.on_press(ButtonId.LEFT)  # cursor=-1
        p.on_press(ButtonId.UP)    # → DATE_TIME
        p.on_press(ButtonId.DOWN)  # → TIME again
        # The typed value is preserved.
        self.assertEqual(p.state.digits[0], 1)
