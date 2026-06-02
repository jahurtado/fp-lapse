"""Tests for the safe-shutdown feature (§7.8 of docs/reference.md).

Four concerns covered:

1. `ButtonRouter` chord detection — BACK+OK arms a separate timer
   that fires after `LONG_PRESS_S` and cancels on either release.
2. App-level dispatch — `on_safe_shutdown_chord()` opens the overlay,
   OK confirms (sets `requested_poweroff`, invokes injected action),
   BACK restores the previous state, and `SHUTTING_DOWN` is inert.
3. `shutdown.do_shutdown` invocation — wraps the right argv and
   swallows missing-binary / EPERM cleanly.
4. Overlay factory smoke — `poweroff_confirm()` returns the spec text.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from typing import List
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse import __main__ as main_mod  # noqa: E402
from fp_lapse.app import App, AppState  # noqa: E402
from fp_lapse.buttons.fake import FakeButtonPanel  # noqa: E402
from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.camera.mock import MockCamera  # noqa: E402
from fp_lapse.configs import ConfigStore  # noqa: E402
from fp_lapse.engine import Engine  # noqa: E402
from fp_lapse.engine_scheduler import EngineScheduler  # noqa: E402
from fp_lapse.ui import poweroff_confirm  # noqa: E402


# Test-only short hold so we don't sit through 3 s per case.
_TEST_HOLD_S: float = 0.05


def _build_app(shutdown_action=None) -> App:
    """Real App + scheduler + mock camera, no schedule wiring."""
    store = ConfigStore.__new__(ConfigStore)
    # ConfigStore needs a path; we want zero IO so monkey it directly.
    store.path = None  # type: ignore[assignment]
    store.backup_path = None  # type: ignore[assignment]
    store.was_reset_from_corruption = False
    store.load = lambda: []  # type: ignore[method-assign]
    camera = MockCamera()
    engine = Engine(camera)
    scheduler = EngineScheduler(engine)
    return App(scheduler=scheduler, store=store, camera=camera,
               shutdown_action=shutdown_action)


# --- 1. Chord detection in ButtonRouter -----------------------------------


class TestChordDetection(unittest.TestCase):
    """The chord timer must arm only when BACK+OK are both held, must
    cancel on any release, and must supersede the OK long-press timer."""

    def _drive(self, hold_s: float = 0):
        """Helper: build router + fake panel, drive chord, observe."""
        chord_calls = []
        lp_calls = []

        class Dummy:
            def on_press(self, _b): pass
            def on_release(self, _b): pass
            def on_long_press(self, _b): lp_calls.append(_b)
            def on_safe_shutdown_chord(self): chord_calls.append(True)

        # Use a very short LONG_PRESS_S so tests stay fast.
        with mock.patch.object(main_mod, "LONG_PRESS_S", _TEST_HOLD_S):
            dirty = threading.Event()
            router = main_mod.ButtonRouter(app=Dummy(), dirty_event=dirty)
            panel = FakeButtonPanel()
            router.attach(panel)
            yield router, panel, chord_calls, lp_calls

    def test_chord_fires_after_back_plus_ok_held(self):
        for _router, panel, chord_calls, _lp_calls in self._drive():
            panel.press(ButtonId.BACK)
            panel.press(ButtonId.OK)
            time.sleep(_TEST_HOLD_S * 2)
            self.assertEqual(len(chord_calls), 1)

    def test_chord_does_not_fire_for_single_button(self):
        for _router, panel, chord_calls, _lp_calls in self._drive():
            panel.press(ButtonId.BACK)
            time.sleep(_TEST_HOLD_S * 2)
            panel.release(ButtonId.BACK)
            self.assertEqual(chord_calls, [])

    def test_chord_cancels_when_back_released_before_threshold(self):
        for _router, panel, chord_calls, _lp_calls in self._drive():
            panel.press(ButtonId.OK)
            panel.press(ButtonId.BACK)
            time.sleep(_TEST_HOLD_S / 2)
            panel.release(ButtonId.BACK)
            time.sleep(_TEST_HOLD_S * 2)
            self.assertEqual(chord_calls, [])

    def test_chord_cancels_when_ok_released_before_threshold(self):
        for _router, panel, chord_calls, _lp_calls in self._drive():
            panel.press(ButtonId.BACK)
            panel.press(ButtonId.OK)
            time.sleep(_TEST_HOLD_S / 2)
            panel.release(ButtonId.OK)
            time.sleep(_TEST_HOLD_S * 2)
            self.assertEqual(chord_calls, [])

    def test_chord_supersedes_ok_long_press(self):
        """A user holding OK for the chord must NOT also pop the
        manage menu (the existing OK long-press) when BACK lands."""
        for _router, panel, chord_calls, lp_calls in self._drive():
            panel.press(ButtonId.OK)        # arms OK long-press
            time.sleep(_TEST_HOLD_S / 4)
            panel.press(ButtonId.BACK)      # should cancel OK long-press
            time.sleep(_TEST_HOLD_S * 2)
            # Chord fired once.
            self.assertEqual(len(chord_calls), 1)
            # OK long-press did NOT fire.
            self.assertEqual(lp_calls, [])


# --- 2. App dispatch ------------------------------------------------------


class TestSafeShutdownDispatch(unittest.TestCase):

    def test_chord_opens_overlay_from_main(self):
        app = _build_app(shutdown_action=lambda: None)
        self.assertEqual(app.state, AppState.MAIN)
        app.on_safe_shutdown_chord()
        self.assertEqual(app.state, AppState.OVERLAY_POWEROFF)

    def test_chord_opens_overlay_from_arbitrary_state(self):
        # The chord is global — any state should yield the overlay.
        app = _build_app(shutdown_action=lambda: None)
        app.state = AppState.TIME_SETUP
        app.on_safe_shutdown_chord()
        self.assertEqual(app.state, AppState.OVERLAY_POWEROFF)
        # Previous state captured so BACK can restore it.
        self.assertEqual(app._prev_state_before_poweroff, AppState.TIME_SETUP)

    def test_chord_no_op_in_overlay_poweroff(self):
        app = _build_app(shutdown_action=lambda: None)
        app.state = AppState.OVERLAY_POWEROFF
        app.on_safe_shutdown_chord()
        # Did not overwrite prev-state with itself.
        self.assertIsNone(app._prev_state_before_poweroff)
        self.assertEqual(app.state, AppState.OVERLAY_POWEROFF)

    def test_chord_no_op_in_shutting_down(self):
        app = _build_app(shutdown_action=lambda: None)
        app.state = AppState.SHUTTING_DOWN
        app.on_safe_shutdown_chord()
        # No state change.
        self.assertEqual(app.state, AppState.SHUTTING_DOWN)

    def test_overlay_ok_triggers_shutdown_and_enters_shutting_down(self):
        calls: List[bool] = []
        app = _build_app(shutdown_action=lambda: calls.append(True))
        app.on_safe_shutdown_chord()
        app._dispatch_overlay_poweroff(True)  # OK confirm
        self.assertEqual(app.state, AppState.SHUTTING_DOWN)
        self.assertEqual(len(calls), 1)

    def test_overlay_back_restores_prev_state(self):
        calls: List[bool] = []
        app = _build_app(shutdown_action=lambda: calls.append(True))
        app.state = AppState.MANAGE
        app.on_safe_shutdown_chord()
        self.assertEqual(app.state, AppState.OVERLAY_POWEROFF)
        app._dispatch_overlay_poweroff(False)  # BACK cancel
        self.assertEqual(app.state, AppState.MANAGE)
        self.assertEqual(calls, [])  # action NOT invoked on cancel

    def test_shutting_down_state_is_inert_to_buttons(self):
        app = _build_app(shutdown_action=lambda: None)
        app.state = AppState.SHUTTING_DOWN
        # Every button in every direction is a no-op here.
        for bid in ButtonId:
            app.on_press(bid)
            app.on_release(bid)
        self.assertEqual(app.state, AppState.SHUTTING_DOWN)

    def test_shutdown_action_exception_does_not_break_state(self):
        def boom():
            raise RuntimeError("simulated /sbin/shutdown failure")
        app = _build_app(shutdown_action=boom)
        app.on_safe_shutdown_chord()
        app._dispatch_overlay_poweroff(True)
        # Even on shutdown-action failure, the UI commits to the
        # SHUTTING_DOWN state so the screen keeps showing the
        # `POWERING OFF…` message — the operator can SSH in to
        # diagnose without the UI flickering back to MAIN.
        self.assertEqual(app.state, AppState.SHUTTING_DOWN)


# --- 3. shutdown.do_shutdown invocation -----------------------------------


class TestShutdownInvocation(unittest.TestCase):

    def test_popens_expected_argv(self):
        from fp_lapse import shutdown as shutdown_mod
        with mock.patch.object(shutdown_mod.subprocess, "Popen") as p:
            shutdown_mod.do_shutdown()
            self.assertEqual(p.call_count, 1)
            args, _ = p.call_args
            self.assertEqual(args[0], ["/sbin/shutdown", "-h", "now"])

    def test_swallows_file_not_found(self):
        from fp_lapse import shutdown as shutdown_mod
        with mock.patch.object(
            shutdown_mod.subprocess, "Popen", side_effect=FileNotFoundError
        ):
            try:
                shutdown_mod.do_shutdown()
            except Exception as e:  # pragma: no cover
                self.fail(f"do_shutdown raised on missing binary: {e}")

    def test_swallows_permission_error(self):
        from fp_lapse import shutdown as shutdown_mod
        with mock.patch.object(
            shutdown_mod.subprocess, "Popen", side_effect=PermissionError
        ):
            try:
                shutdown_mod.do_shutdown()
            except Exception as e:  # pragma: no cover
                self.fail(f"do_shutdown raised on EPERM: {e}")


# --- 4. Overlay factory ---------------------------------------------------


class TestPoweroffConfirmFactory(unittest.TestCase):

    def test_title_matches_spec(self):
        d = poweroff_confirm()
        self.assertEqual(d.title, "Power off?")
        # No body line — the title alone carries the question.
        self.assertIsNone(d.body)
        # Inherits the standard hint from OverlayDialog defaults.
        self.assertEqual(d.hint, "OK yes        ESC no")


if __name__ == "__main__":
    unittest.main()
