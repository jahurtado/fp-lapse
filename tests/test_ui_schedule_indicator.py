"""Tests for `ScheduleIndicator` enum and status-bar wiring."""

from __future__ import annotations

import os
import sys
import unittest

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.ui.schedule_indicator import ScheduleIndicator  # noqa: E402
from fp_lapse.ui.widgets import status_bar  # noqa: E402


def _draw_status(state: ScheduleIndicator) -> bytes:
    img = Image.new("RGB", (WIDTH, HEIGHT), (10, 14, 20))
    draw = ImageDraw.Draw(img)
    status_bar(
        draw,
        time_str="18:42:07",
        cam_connected=True,
        skips=0,
        show_skips=False,           # IDLE → no SKIPS, indicator hugs right edge
        schedule_state=state,
    )
    return img.tobytes()


class TestScheduleIndicatorEnum(unittest.TestCase):
    def test_has_four_members(self):
        members = list(ScheduleIndicator)
        self.assertEqual(len(members), 4)

    def test_members_are_off_red_green_yellow(self):
        self.assertEqual(
            set(ScheduleIndicator),
            {
                ScheduleIndicator.OFF,
                ScheduleIndicator.RED,
                ScheduleIndicator.GREEN,
                ScheduleIndicator.YELLOW,
            },
        )

    def test_stringifiable(self):
        for s in ScheduleIndicator:
            # str(enum) returns the value via the (str, Enum) mixin.
            self.assertIsInstance(str(s), str)


class TestStatusBarIndicatorRendering(unittest.TestCase):
    def test_off_renders_nothing_extra(self):
        # With OFF, the indicator region renders no glyph and no dot —
        # the bytes match a "schedule_state default" call.
        img = Image.new("RGB", (WIDTH, HEIGHT), (10, 14, 20))
        draw = ImageDraw.Draw(img)
        status_bar(
            draw, time_str="18:42:07",
            cam_connected=True, skips=0, show_skips=False,
        )
        baseline = img.tobytes()
        with_off = _draw_status(ScheduleIndicator.OFF)
        self.assertEqual(baseline, with_off)

    def test_smoke_each_state_renders(self):
        for s in ScheduleIndicator:
            _draw_status(s)  # must not raise

    def test_red_green_yellow_produce_distinct_pixels(self):
        red = _draw_status(ScheduleIndicator.RED)
        green = _draw_status(ScheduleIndicator.GREEN)
        yellow = _draw_status(ScheduleIndicator.YELLOW)
        self.assertNotEqual(red, green)
        self.assertNotEqual(red, yellow)
        self.assertNotEqual(green, yellow)

    def test_off_differs_from_non_off(self):
        off = _draw_status(ScheduleIndicator.OFF)
        for s in (
            ScheduleIndicator.RED,
            ScheduleIndicator.GREEN,
            ScheduleIndicator.YELLOW,
        ):
            self.assertNotEqual(off, _draw_status(s))


if __name__ == "__main__":
    unittest.main()
