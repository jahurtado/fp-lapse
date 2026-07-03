"""Tests for the semiautomatic bracketing generator screen.

Interaction (cursor clamp, LEFT/RIGHT cycling incl. the iso2 off sentinel
and the direction toggle, preview recompute, ACCEPT/CANCEL) + a
pixel-exact visual regression against the committed PNGs.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.configs import Shot  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.ui.bracket_screen import (  # noqa: E402
    BracketGenAction,
    BracketGenInteraction,
    BracketGenState,
    render_bracket_gen,
)
from fp_lapse.ui.edit_values import ISO2_OFF, ISO_VALUES  # noqa: E402


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "test_artifacts"


def _ix(reference=None, config_name="Totality") -> BracketGenInteraction:
    if reference is None:
        reference = Shot(1 / 500, 400, 8.0)
    return BracketGenInteraction(reference=reference, config_name=config_name)


class TestDefaults(unittest.TestCase):
    def test_seeded_from_reference(self):
        ix = _ix()
        s = ix.state
        self.assertEqual(s.reference, Shot(1 / 500, 400, 8.0))
        self.assertFalse(s.brightest)        # darkest
        self.assertEqual(s.ev_step, 1)
        self.assertEqual(s.n, 5)
        self.assertEqual(s.iso1, 400)        # the reference ISO
        self.assertIsNone(s.iso2)            # off
        self.assertEqual(s.field_cursor, 0)
        self.assertEqual(s.config_name, "Totality")


class TestCursor(unittest.TestCase):
    def test_up_clamps_at_zero(self):
        ix = _ix()
        ix.on_press(ButtonId.UP)
        self.assertEqual(ix.state.field_cursor, 0)

    def test_down_advances(self):
        ix = _ix()
        ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.state.field_cursor, 1)

    def test_down_clamps_at_last_field(self):
        ix = _ix()
        for _ in range(20):
            ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.state.field_cursor, 7)


class TestCycling(unittest.TestCase):
    def _at(self, idx: int) -> BracketGenInteraction:
        ix = _ix()
        for _ in range(idx):
            ix.on_press(ButtonId.DOWN)
        return ix

    def test_ref_shutter_cycles(self):
        ix = self._at(0)
        ix.on_press(ButtonId.RIGHT)
        self.assertNotEqual(ix.state.reference.shutter, 1 / 500)

    def test_ref_iso_cycles(self):
        ix = self._at(1)
        ix.on_press(ButtonId.RIGHT)
        self.assertNotEqual(ix.state.reference.iso, 400)

    def test_ref_aperture_cycles(self):
        ix = self._at(2)
        ix.on_press(ButtonId.RIGHT)
        self.assertNotEqual(ix.state.reference.aperture, 8.0)

    def test_direction_toggles(self):
        ix = self._at(3)
        self.assertFalse(ix.state.brightest)
        ix.on_press(ButtonId.RIGHT)
        self.assertTrue(ix.state.brightest)
        ix.on_press(ButtonId.RIGHT)
        self.assertFalse(ix.state.brightest)
        # LEFT also toggles (only two values).
        ix.on_press(ButtonId.LEFT)
        self.assertTrue(ix.state.brightest)

    def test_ev_step_cycles_including_half_steps(self):
        ix = self._at(4)
        ix.on_press(ButtonId.RIGHT)   # 1 → 2
        self.assertEqual(ix.state.ev_step, 2)
        ix.on_press(ButtonId.RIGHT)   # 2 → 2.5
        self.assertEqual(ix.state.ev_step, 2.5)

    def test_shots_cycles_one_to_nine_no_auto(self):
        ix = self._at(5)
        # 5 → wrap down to ... LEFT from 5 → 4
        ix.on_press(ButtonId.LEFT)
        self.assertEqual(ix.state.n, 4)
        # Cycle up to 9 then wrap to 1 (no auto sentinel).
        for _ in range(5):
            ix.on_press(ButtonId.RIGHT)
        self.assertEqual(ix.state.n, 9)
        ix.on_press(ButtonId.RIGHT)
        self.assertEqual(ix.state.n, 1)

    def test_iso1_cycles(self):
        ix = self._at(6)
        ix.on_press(ButtonId.RIGHT)
        self.assertNotEqual(ix.state.iso1, 400)

    def test_iso2_cycles_into_off_sentinel(self):
        ix = self._at(7)
        # iso2 starts off → None in state.
        self.assertIsNone(ix.state.iso2)
        # RIGHT from off lands on the first ISO.
        ix.on_press(ButtonId.RIGHT)
        self.assertEqual(ix.state.iso2, ISO_VALUES[0])
        # LEFT back to off.
        ix.on_press(ButtonId.LEFT)
        self.assertIsNone(ix.state.iso2)
        # LEFT again wraps to the highest ISO.
        ix.on_press(ButtonId.LEFT)
        self.assertEqual(ix.state.iso2, ISO_VALUES[-1])


class TestPreviewRecompute(unittest.TestCase):
    def test_preview_changes_with_parameters(self):
        ix = _ix()
        before = ix.result()
        self.assertEqual(len(before.shots), 5)
        # Move to shots and shrink to 3.
        for _ in range(5):
            ix.on_press(ButtonId.DOWN)
        ix.on_press(ButtonId.LEFT)   # 5 → 4
        ix.on_press(ButtonId.LEFT)   # 4 → 3
        after = ix.result()
        self.assertEqual(after.requested, 3)
        self.assertLessEqual(len(after.shots), 3)

    def test_result_matches_state(self):
        ix = _ix()
        r = ix.result()
        # Default clean ladder: 5 surviving, none dropped.
        self.assertEqual(r.dropped, 0)
        self.assertEqual(len(r.shots), 5)


class TestActions(unittest.TestCase):
    def test_ok_returns_accept(self):
        self.assertEqual(_ix().on_press(ButtonId.OK), BracketGenAction.ACCEPT)

    def test_back_returns_cancel(self):
        self.assertEqual(_ix().on_press(ButtonId.BACK), BracketGenAction.CANCEL)

    def test_navigation_returns_none(self):
        ix = _ix()
        for b in (ButtonId.UP, ButtonId.DOWN, ButtonId.LEFT, ButtonId.RIGHT):
            self.assertIsNone(ix.on_press(b))


class TestRenderSmoke(unittest.TestCase):
    def test_renders_320x240_rgb(self):
        img = render_bracket_gen(_ix().state)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")

    def test_renders_dropped_state(self):
        state = BracketGenState(
            reference=Shot(1 / 1000, 200, 5.6),
            brightest=True, ev_step=3, n=5, iso1=100, iso2=None,
            field_cursor=3, config_name="Corona",
        )
        img = render_bracket_gen(state)
        self.assertEqual(img.size, (WIDTH, HEIGHT))


def _dump_artifacts(name: str, expected: Image.Image, actual: Image.Image) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    expected.save(ARTIFACTS_DIR / f"{name}_expected.png")
    actual.save(ARTIFACTS_DIR / f"{name}_actual.png")
    return ARTIFACTS_DIR


class TestVisualRegression(unittest.TestCase):
    """Pixel-exact match against the committed generator-screen PNGs."""

    def test_bracket_gen_preview(self):
        state = BracketGenState(
            reference=Shot(1 / 500, 400, 8.0),
            brightest=False, ev_step=1, n=5, iso1=400, iso2=None,
            field_cursor=7, config_name="Totality",
        )
        actual = render_bracket_gen(state)
        expected_path = MOCKUPS_DIR / "28_bracket_gen_preview.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("28_bracket_gen_preview", expected, actual)
            self.fail(f"28_bracket_gen_preview.png differs — see {out}")

    def test_bracket_gen_dropped(self):
        state = BracketGenState(
            reference=Shot(1 / 1000, 200, 5.6),
            brightest=True, ev_step=3, n=5, iso1=100, iso2=None,
            field_cursor=3, config_name="Corona",
        )
        actual = render_bracket_gen(state)
        expected_path = MOCKUPS_DIR / "29_bracket_gen_dropped.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("29_bracket_gen_dropped", expected, actual)
            self.fail(f"29_bracket_gen_dropped.png differs — see {out}")


if __name__ == "__main__":
    unittest.main()
