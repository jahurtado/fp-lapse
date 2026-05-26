"""Tests for `FakeButtonPanel`."""

from __future__ import annotations

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons import ButtonId  # noqa: E402
from fp_lapse.buttons.fake import FakeButtonPanel  # noqa: E402


class TestFakeButtonPanel(unittest.TestCase):
    def test_starts_unpressed(self):
        p = FakeButtonPanel()
        for bid in ButtonId:
            self.assertFalse(p.is_pressed(bid))
        self.assertEqual(p.states(), {bid: False for bid in ButtonId})

    def test_press_and_release_flip_state(self):
        p = FakeButtonPanel()
        p.press(ButtonId.OK)
        self.assertTrue(p.is_pressed(ButtonId.OK))
        p.release(ButtonId.OK)
        self.assertFalse(p.is_pressed(ButtonId.OK))

    def test_press_increments_count_once_per_real_press(self):
        p = FakeButtonPanel()
        p.press(ButtonId.UP)
        p.press(ButtonId.UP)   # already pressed, should not re-fire
        p.release(ButtonId.UP)
        p.press(ButtonId.UP)
        self.assertEqual(p.press_count[ButtonId.UP], 2)

    def test_tap_is_press_then_release(self):
        p = FakeButtonPanel()
        events = []
        p.on_press(ButtonId.OK, lambda: events.append("p"))
        p.on_release(ButtonId.OK, lambda: events.append("r"))
        p.tap(ButtonId.OK)
        self.assertEqual(events, ["p", "r"])
        self.assertFalse(p.is_pressed(ButtonId.OK))

    def test_on_press_callback_fires(self):
        p = FakeButtonPanel()
        seen = []
        p.on_press(ButtonId.DOWN, lambda: seen.append("down!"))
        p.press(ButtonId.DOWN)
        self.assertEqual(seen, ["down!"])

    def test_on_release_callback_fires(self):
        p = FakeButtonPanel()
        seen = []
        p.on_release(ButtonId.LEFT, lambda: seen.append("released"))
        p.press(ButtonId.LEFT)
        p.release(ButtonId.LEFT)
        self.assertEqual(seen, ["released"])

    def test_hold_sleeps_approximately(self):
        p = FakeButtonPanel()
        t0 = time.monotonic()
        p.hold(ButtonId.OK, 0.1)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.09)
        self.assertLess(elapsed, 0.3)
        self.assertFalse(p.is_pressed(ButtonId.OK))

    def test_release_unpressed_button_is_noop(self):
        p = FakeButtonPanel()
        events = []
        p.on_release(ButtonId.OK, lambda: events.append("r"))
        p.release(ButtonId.OK)
        self.assertEqual(events, [])

    def test_double_press_does_not_double_fire(self):
        p = FakeButtonPanel()
        events = []
        p.on_press(ButtonId.UP, lambda: events.append("p"))
        p.press(ButtonId.UP)
        p.press(ButtonId.UP)
        self.assertEqual(events, ["p"])


if __name__ == "__main__":
    unittest.main()
