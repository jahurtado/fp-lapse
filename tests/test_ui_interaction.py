"""Tests for the per-screen button handlers.

Covers:

- `MainScreenInteraction`: cursor (clamp up/down, on + New), short OK
  (start / switch / no-op / open_edit_new), long OK (open manage /
  no-op on + New), BACK (stop_confirm in RUNNING / no-op in IDLE),
  `reset_input` clears state.
- `handle_overlay_button`: OK=True, BACK=False, others=None.
- `ManageMenuInteraction`: cursor (clamp), OK on each position returns
  the right action, BACK = Cancel.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import EngineState  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    MENU_ITEMS,
    EditAction,
    EditScreenInteraction,
    MainAction,
    MainActionResult,
    MainScreenInteraction,
    ManageMenuAction,
    ManageMenuInteraction,
    handle_overlay_button,
)
from fp_lapse.ui.edit_values import (  # noqa: E402
    INTERVALS_S,
    ISO_VALUES,
    SHUTTER_VALUES,
    cycle_in_list,
)


# Fixtures
A = TimelapseConfig("A", 10.0, (Shot(shutter=1 / 500, iso=200),))
B = TimelapseConfig("B", 5.0, (Shot(shutter=1 / 1000, iso=400),))
C = TimelapseConfig("C", 30.0, (Shot(shutter=2.0, iso=1600),))


# ----------------------------------------------------------------------
# MainScreenInteraction — cursor
# ----------------------------------------------------------------------


class TestMainCursor(unittest.TestCase):
    def test_starts_at_zero(self):
        ix = MainScreenInteraction()
        self.assertEqual(ix.cursor, 0)

    def test_down_advances(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.DOWN,
            configs=(A, B, C), engine_state=EngineState.IDLE,
        )
        self.assertEqual(ix.cursor, 1)

    def test_down_clamps_at_new_item(self):
        ix = MainScreenInteraction()
        ix.cursor = 3  # already on "+ New" with 3 configs
        ix.on_press(
            ButtonId.DOWN,
            configs=(A, B, C), engine_state=EngineState.IDLE,
        )
        self.assertEqual(ix.cursor, 3)

    def test_up_clamps_at_zero(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.UP,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        self.assertEqual(ix.cursor, 0)

    def test_cursor_traverses_new_item(self):
        ix = MainScreenInteraction()
        # 2 configs + "+ New" = slots 0,1,2
        for _ in range(3):
            ix.on_press(
                ButtonId.DOWN,
                configs=(A, B), engine_state=EngineState.IDLE,
            )
        self.assertEqual(ix.cursor, 2)  # on +New

    def test_horizontal_buttons_reserved(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.LEFT,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        ix.on_press(
            ButtonId.RIGHT,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        self.assertEqual(ix.cursor, 0)


# ----------------------------------------------------------------------
# MainScreenInteraction — OK corto
# ----------------------------------------------------------------------


class TestMainOKShort(unittest.TestCase):
    def test_ok_short_idle_on_config_starts(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
            active_config_name=None,
        )
        self.assertEqual(result, MainActionResult(MainAction.START, cfg=A))

    def test_ok_short_running_on_other_switches(self):
        ix = MainScreenInteraction()
        ix.cursor = 1
        ix.on_press(
            ButtonId.OK,
            configs=(A, B), engine_state=EngineState.RUNNING,
        )
        result = ix.on_release(
            ButtonId.OK,
            configs=(A, B), engine_state=EngineState.RUNNING,
            active_config_name="A",
        )
        self.assertEqual(result, MainActionResult(MainAction.SWITCH, cfg=B))

    def test_ok_short_running_on_running_is_noop(self):
        ix = MainScreenInteraction()
        ix.cursor = 0
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.RUNNING,
        )
        result = ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.RUNNING,
            active_config_name="A",
        )
        self.assertIsNone(result)

    def test_ok_short_on_new_opens_edit(self):
        ix = MainScreenInteraction()
        ix.cursor = 1  # on "+ New" given 1 config
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
            active_config_name=None,
        )
        self.assertEqual(result, MainActionResult(MainAction.OPEN_EDIT_NEW))


# ----------------------------------------------------------------------
# MainScreenInteraction — OK largo
# ----------------------------------------------------------------------


class TestMainOKLong(unittest.TestCase):
    # Long-press detection is external (driven by a `threading.Timer`
    # in `__main__`). The interaction exposes `on_long_press()` which
    # is called when the 3 s timer fires while OK is still held.

    def test_on_long_press_without_ok_press_is_noop(self):
        ix = MainScreenInteraction()
        result = ix.on_long_press(ButtonId.OK, configs=(A,))
        self.assertIsNone(result)

    def test_on_long_press_after_ok_press_fires_open_manage(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_long_press(ButtonId.OK, configs=(A,))
        self.assertEqual(
            result, MainActionResult(MainAction.OPEN_MANAGE, cfg=A)
        )

    def test_release_after_long_press_is_noop(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        ix.on_long_press(ButtonId.OK, configs=(A,))
        # The trailing release after a long-press must be a no-op so
        # we don't also fire the short-press action.
        release = ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
            active_config_name=None,
        )
        self.assertIsNone(release)

    def test_long_press_for_non_ok_button_is_noop(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_long_press(ButtonId.DOWN, configs=(A,))
        self.assertIsNone(result)

    def test_long_press_after_release_is_noop(self):
        # If the timer races with the release and fires *after* the
        # user already let OK go, we must not emit a stale long-press
        # action.
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
            active_config_name=None,
        )
        result = ix.on_long_press(ButtonId.OK, configs=(A,))
        self.assertIsNone(result)

    def test_long_press_on_new_is_noop(self):
        ix = MainScreenInteraction()
        ix.cursor = 1  # on +New
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_long_press(ButtonId.OK, configs=(A,))
        self.assertIsNone(result)  # §7.1: no manage menu over "+ New"

    def test_short_press_does_not_trigger_long_press(self):
        # Short press = press + release without the timer firing.
        # Verify the action returned is short-press semantics.
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        result = ix.on_release(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
            active_config_name=None,
        )
        self.assertEqual(
            result, MainActionResult(MainAction.START, cfg=A)
        )



# ----------------------------------------------------------------------
# MainScreenInteraction — BACK
# ----------------------------------------------------------------------


class TestMainBack(unittest.TestCase):
    def test_back_in_running_requests_stop_confirm(self):
        ix = MainScreenInteraction()
        result = ix.on_press(
            ButtonId.BACK,
            configs=(A,), engine_state=EngineState.RUNNING,
        )
        self.assertEqual(result, MainActionResult(MainAction.STOP_CONFIRM))

    def test_back_in_idle_is_noop(self):
        ix = MainScreenInteraction()
        result = ix.on_press(
            ButtonId.BACK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        self.assertIsNone(result)


class TestMainResetInput(unittest.TestCase):
    def test_reset_clears_pending_long_press(self):
        ix = MainScreenInteraction()
        ix.on_press(
            ButtonId.OK,
            configs=(A,), engine_state=EngineState.IDLE,
        )
        ix.reset_input()
        # After reset, a stale long-press timer firing must be a no-op.
        result = ix.on_long_press(ButtonId.OK, configs=(A,))
        self.assertIsNone(result)


# ----------------------------------------------------------------------
# Overlay handler
# ----------------------------------------------------------------------


class TestHandleOverlayButton(unittest.TestCase):
    def test_ok_returns_true(self):
        self.assertTrue(handle_overlay_button(ButtonId.OK))

    def test_back_returns_false(self):
        self.assertFalse(handle_overlay_button(ButtonId.BACK))

    def test_other_buttons_return_none(self):
        for b in (ButtonId.UP, ButtonId.DOWN, ButtonId.LEFT, ButtonId.RIGHT):
            self.assertIsNone(handle_overlay_button(b))


# ----------------------------------------------------------------------
# ManageMenuInteraction
# ----------------------------------------------------------------------


class TestManageMenu(unittest.TestCase):
    def test_starts_at_first_item(self):
        ix = ManageMenuInteraction()
        self.assertEqual(ix.cursor, 0)

    def test_down_advances_clamping(self):
        ix = ManageMenuInteraction()
        for _ in range(len(MENU_ITEMS) + 2):
            ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.cursor, len(MENU_ITEMS) - 1)

    def test_up_at_first_stays(self):
        ix = ManageMenuInteraction()
        ix.on_press(ButtonId.UP)
        self.assertEqual(ix.cursor, 0)

    def test_ok_on_each_position_returns_correct_action(self):
        expected = (
            ManageMenuAction.EDIT,
            ManageMenuAction.DUPLICATE,
            ManageMenuAction.DELETE,
            ManageMenuAction.CANCEL,
        )
        for i, exp in enumerate(expected):
            with self.subTest(i=i):
                ix = ManageMenuInteraction()
                ix.cursor = i
                self.assertEqual(ix.on_press(ButtonId.OK), exp)

    def test_back_is_cancel(self):
        ix = ManageMenuInteraction()
        ix.cursor = 0
        self.assertEqual(
            ix.on_press(ButtonId.BACK), ManageMenuAction.CANCEL,
        )

    def test_left_right_ignored(self):
        ix = ManageMenuInteraction()
        ix.cursor = 1
        for b in (ButtonId.LEFT, ButtonId.RIGHT):
            self.assertIsNone(ix.on_press(b))
        self.assertEqual(ix.cursor, 1)

    def test_reset_returns_to_first_item(self):
        ix = ManageMenuInteraction()
        ix.cursor = 2
        ix.reset()
        self.assertEqual(ix.cursor, 0)


# ----------------------------------------------------------------------
# Edit value cycling helpers
# ----------------------------------------------------------------------


class TestCycleInList(unittest.TestCase):
    def test_advance_one(self):
        self.assertEqual(cycle_in_list(1, [1, 2, 3], 1), 2)

    def test_wrap_around_forward(self):
        self.assertEqual(cycle_in_list(3, [1, 2, 3], 1), 1)

    def test_wrap_around_backward(self):
        self.assertEqual(cycle_in_list(1, [1, 2, 3], -1), 3)

    def test_value_not_in_list_snaps_to_closest_numeric(self):
        self.assertEqual(cycle_in_list(7, [None, "auto", 5, 10, 15], 0), 5)
        self.assertEqual(cycle_in_list(13, [None, "auto", 5, 10, 15], 0), 15)

    def test_value_not_in_list_non_numeric_returns_first(self):
        self.assertEqual(cycle_in_list("xx", [None, "auto", 5], 0), None)

    def test_null_passes_through(self):
        self.assertEqual(cycle_in_list(None, [None, "auto", 1], 1), "auto")


# ----------------------------------------------------------------------
# EditScreenInteraction
# ----------------------------------------------------------------------


class TestEditInteractionBasics(unittest.TestCase):
    def setUp(self):
        self.cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        self.ix = EditScreenInteraction(self.cfg)

    def test_starts_at_first_field(self):
        self.assertEqual(self.ix.field_cursor, 0)
        self.assertEqual(self.ix.scroll_offset, 0)
        self.assertFalse(self.ix.is_dirty)

    def test_down_advances_cursor(self):
        self.ix.on_press(ButtonId.DOWN)
        self.assertEqual(self.ix.field_cursor, 1)

    def test_up_at_zero_stays(self):
        self.ix.on_press(ButtonId.UP)
        self.assertEqual(self.ix.field_cursor, 0)

    def test_down_clamps_at_last_field(self):
        # 3 header + 3 shot params = 6 fields total
        for _ in range(20):
            self.ix.on_press(ButtonId.DOWN)
        self.assertEqual(self.ix.field_cursor, 5)

    def test_ok_returns_save(self):
        self.assertEqual(self.ix.on_press(ButtonId.OK), EditAction.SAVE)

    def test_back_returns_back(self):
        self.assertEqual(self.ix.on_press(ButtonId.BACK), EditAction.BACK)


class TestEditCycling(unittest.TestCase):
    """Mapping: ↑↓ navega campos, ←→ cicla el valor del campo activo."""

    def setUp(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        self.ix = EditScreenInteraction(cfg)

    def _move_to_field(self, idx: int) -> None:
        for _ in range(idx):
            self.ix.on_press(ButtonId.DOWN)

    def test_name_field_is_read_only(self):
        # Field 0 = name; cycling no debe modificar nada.
        self.ix.on_press(ButtonId.RIGHT)
        self.assertEqual(self.ix.draft.name, "X")
        self.assertFalse(self.ix.is_dirty)

    def test_interval_cycles_through_list(self):
        self._move_to_field(1)  # interval
        self.ix.on_press(ButtonId.RIGHT)
        # 10 → next in INTERVALS_S = 15
        self.assertEqual(self.ix.draft.interval_s, 15.0)
        self.assertTrue(self.ix.is_dirty)

    def test_shots_right_adds_inheriting_shot(self):
        self._move_to_field(2)  # shots
        self.ix.on_press(ButtonId.RIGHT)
        self.assertEqual(len(self.ix.draft.shots), 2)
        # New shot inherits previous values
        self.assertEqual(self.ix.draft.shots[1], self.ix.draft.shots[0])

    def test_shots_left_removes_last(self):
        cfg = TimelapseConfig("X", 10.0, (
            Shot(shutter=1 / 500, iso=200),
            Shot(shutter=1 / 250, iso=200),
            Shot(shutter=1 / 125, iso=200),
        ))
        ix = EditScreenInteraction(cfg)
        for _ in range(2):
            ix.on_press(ButtonId.DOWN)
        # cursor on shots
        ix.on_press(ButtonId.LEFT)
        self.assertEqual(len(ix.draft.shots), 2)

    def test_shots_left_from_1_goes_to_auto(self):
        # Starting at 1 manual shot, LEFT cycles to "1 (auto)" → empty
        # shots tuple. The previously configured shots are kept in a
        # snapshot for round-trip restoration.
        self._move_to_field(2)
        self.ix.on_press(ButtonId.LEFT)
        self.assertTrue(self.ix.draft.is_auto)
        self.assertEqual(self.ix.draft.shots, ())

    def test_shots_auto_to_manual_restores_snapshot(self):
        cfg = TimelapseConfig("X", 10.0, (
            Shot(shutter=1 / 500, iso=200),
            Shot(shutter=1 / 250, iso=400),
        ))
        ix = EditScreenInteraction(cfg)
        for _ in range(2):
            ix.on_press(ButtonId.DOWN)  # cursor on shots
        # LEFT: 2 → 1
        ix.on_press(ButtonId.LEFT)
        self.assertEqual(len(ix.draft.shots), 1)
        # LEFT: 1 → auto
        ix.on_press(ButtonId.LEFT)
        self.assertTrue(ix.draft.is_auto)
        # RIGHT: auto → 1, then 1 → 2, restoring the original second shot.
        ix.on_press(ButtonId.RIGHT)
        self.assertEqual(len(ix.draft.shots), 1)
        ix.on_press(ButtonId.RIGHT)
        self.assertEqual(len(ix.draft.shots), 2)
        self.assertEqual(ix.draft.shots[1].shutter, 1 / 250)

    def test_shots_right_from_9_wraps_to_auto(self):
        cfg = TimelapseConfig("X", 10.0, tuple(
            Shot(shutter=1 / 500, iso=200) for _ in range(9)
        ))
        ix = EditScreenInteraction(cfg)
        for _ in range(2):
            ix.on_press(ButtonId.DOWN)
        ix.on_press(ButtonId.RIGHT)  # 9 → wraps to auto
        self.assertTrue(ix.draft.is_auto)

    def test_shutter_cycle_modifies_draft(self):
        self._move_to_field(3)  # #1 shutter
        self.ix.on_press(ButtonId.RIGHT)
        # 1/500 → next in SHUTTER_VALUES = 1/400
        self.assertNotEqual(self.ix.draft.shots[0].shutter, 1 / 500)
        self.assertTrue(self.ix.is_dirty)

    def test_iso_cycle_modifies_draft(self):
        self._move_to_field(4)  # #1 iso
        original_iso = self.ix.draft.shots[0].iso
        self.ix.on_press(ButtonId.RIGHT)
        self.assertNotEqual(self.ix.draft.shots[0].iso, original_iso)


if __name__ == "__main__":
    unittest.main()
