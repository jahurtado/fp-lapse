"""Tests for the status-bar model label + dial-mismatch warning.

The status bar used to hardcode the camera label `"fp"`. With Nikon support
it must reflect the detected camera (`D5600` vs `fp`) and show a
`DIAL NOT ON M` warning when the engine wants MANUAL/PROGRAM but the D5600
mode dial disagrees.

These are pure-render checks (no hardware): we drive `status_bar` /
`MainScreen.render` with the new fields and assert the pixels change. The
byte-exact mockup regression tests live in `test_ui_main_screen.py`.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.display.iface import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.engine import EngineState  # noqa: E402
from fp_lapse.ui import MainScreen, UIState, widgets  # noqa: E402


def _render_bar(**kwargs) -> bytes:
    canvas = new_canvas((10, 14, 20))
    draw = ImageDraw.Draw(canvas)
    base = dict(time_str="14:32:07", cam_connected=True, skips=0, show_skips=False)
    base.update(kwargs)
    widgets.status_bar(draw, **base)
    return canvas.tobytes()


class TestStatusBarModelLabel(unittest.TestCase):
    def test_default_label_is_fp(self):
        # Backwards-compatible default keeps the Sigma's "fp" label.
        canvas = new_canvas((10, 14, 20))
        draw = ImageDraw.Draw(canvas)
        widgets.status_bar(draw, time_str="14:32:07", cam_connected=True)
        # Should not raise; image is the right size.
        self.assertEqual(canvas.size, (WIDTH, HEIGHT))

    def test_model_label_changes_pixels(self):
        fp = _render_bar(model_label="fp")
        d5600 = _render_bar(model_label="D5600")
        self.assertNotEqual(fp, d5600)

    def test_model_label_accepts_arbitrary_string(self):
        # No crash with a longer label.
        _render_bar(model_label="D5600")


class TestStatusBarDialWarning(unittest.TestCase):
    def test_dial_warning_changes_pixels(self):
        without = _render_bar(model_label="D5600", dial_mismatch=False)
        with_warn = _render_bar(model_label="D5600", dial_mismatch=True)
        self.assertNotEqual(without, with_warn)

    def test_dial_warning_default_off(self):
        # Default keeps the bar clean (no warning) — Sigma path unaffected.
        a = _render_bar(model_label="fp")
        b = _render_bar(model_label="fp", dial_mismatch=False)
        self.assertEqual(a, b)


class TestMainScreenWiresLabelAndWarning(unittest.TestCase):
    def _state(self, **over) -> UIState:
        base = dict(
            configs=(),
            cursor=0,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="14:32:07",
        )
        base.update(over)
        return UIState(**base)

    def test_uistate_has_model_label_default(self):
        st = self._state()
        self.assertEqual(st.camera_model_label, "fp")

    def test_uistate_has_dial_mismatch_default(self):
        st = self._state()
        self.assertFalse(st.dial_mismatch)

    def test_model_label_propagates_to_render(self):
        fp = MainScreen().render(self._state(camera_model_label="fp")).tobytes()
        d5600 = MainScreen().render(self._state(camera_model_label="D5600")).tobytes()
        self.assertNotEqual(fp, d5600)

    def test_dial_mismatch_propagates_to_render(self):
        base = self._state(camera_model_label="D5600")
        clean = MainScreen().render(base).tobytes()
        warned = MainScreen().render(replace(base, dial_mismatch=True)).tobytes()
        self.assertNotEqual(clean, warned)


if __name__ == "__main__":
    unittest.main()
