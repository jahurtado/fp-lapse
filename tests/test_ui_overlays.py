"""Tests for the modal overlays (`render_overlay` + factories).

Smoke + pixel-exact visual regression against
`05_overlay_stop_confirm.png`. The regression needs to rebuild the
base (main screen RUNNING) identical to the mockup's; this is done
with `MainScreen.render(...)` and the same fixtures as
`test_ui_main_screen.py`.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.engine import EngineState  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    MainScreen,
    UIState,
    delete_confirm,
    discard_changes,
    render_overlay,
    stop_confirm,
)
from fp_lapse.ui.overlays import OverlayDialog  # noqa: E402


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "test_artifacts"


PARTIAL = TimelapseConfig(
    name="Partial", interval_s=10.0,
    shots=(Shot(shutter=1 / 1000, iso=200, aperture=None),),
)
TOTALITY = TimelapseConfig(
    name="Totality", interval_s=5.0,
    shots=(
        Shot(shutter=1 / 500, iso=400, aperture=None),
        Shot(shutter=1 / 125, iso=400, aperture=None),
        Shot(shutter=1 / 30,  iso=400, aperture=None),
        Shot(shutter=1 / 8,   iso=400, aperture=None),
        Shot(shutter=2.0,     iso="auto", aperture=None),
    ),
)


def _running_main_screen() -> Image.Image:
    """Reconstruye exactamente el base del mockup 02 (= base del 05)."""
    state = UIState(
        configs=(TOTALITY, PARTIAL),
        cursor=0,
        engine_state=EngineState.RUNNING,
        active_config_name="Totality",
        shots_taken=142,
        seconds_to_next_shot=4.3,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
    )
    return MainScreen().render(state)


def _dump_artifacts(name: str, expected: Image.Image, actual: Image.Image) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    expected.save(ARTIFACTS_DIR / f"{name}_expected.png")
    actual.save(ARTIFACTS_DIR / f"{name}_actual.png")
    diff = Image.new("RGB", expected.size, (0, 0, 0))
    ep = expected.convert("RGB").load()
    ap = actual.convert("RGB").load()
    dp = diff.load()
    for j in range(expected.size[1]):
        for i in range(expected.size[0]):
            if ep[i, j] != ap[i, j]:
                dp[i, j] = (255, 0, 255)
    diff.save(ARTIFACTS_DIR / f"{name}_diff.png")
    return ARTIFACTS_DIR


class TestOverlayFactories(unittest.TestCase):
    def test_stop_confirm_text(self):
        d = stop_confirm()
        self.assertEqual(d.title, "Stop the timelapse?")
        self.assertEqual(d.body, "Sync will be lost.")
        self.assertEqual(d.hint, "OK yes        BACK no")

    def test_discard_changes_has_no_body(self):
        d = discard_changes()
        self.assertEqual(d.title, "Discard changes?")
        self.assertIsNone(d.body)

    def test_delete_confirm_includes_name(self):
        d = delete_confirm("Totality")
        self.assertEqual(d.title, "Delete 'Totality'?")
        self.assertIsNone(d.body)


class TestRenderOverlaySmoke(unittest.TestCase):
    def test_renders_320x240_rgb(self):
        base = _running_main_screen()
        out = render_overlay(base, stop_confirm())
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_base_must_be_correct_size(self):
        wrong = Image.new("RGB", (100, 100), (0, 0, 0))
        with self.assertRaises(ValueError):
            render_overlay(wrong, stop_confirm())

    def test_body_optional(self):
        base = _running_main_screen()
        # discard_changes() has no body; should not crash.
        out = render_overlay(base, discard_changes())
        self.assertEqual(out.size, (WIDTH, HEIGHT))

    def test_overlay_modifies_base(self):
        # Sanity: rendering an overlay should produce a visibly different
        # image than the base (different pixel bytes).
        base = _running_main_screen()
        overlayed = render_overlay(base, stop_confirm())
        self.assertNotEqual(base.tobytes(), overlayed.tobytes())


class TestOverlayVisualRegression(unittest.TestCase):
    """Pixel-exact match contra `docs/mockups/05_overlay_stop_confirm.png`."""

    def test_05_overlay_stop_confirm(self):
        actual = render_overlay(_running_main_screen(), stop_confirm())
        expected_path = MOCKUPS_DIR / "05_overlay_stop_confirm.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("05_overlay_stop_confirm", expected, actual)
            self.fail(
                f"05_overlay_stop_confirm.png differs from production render — "
                f"see {out}/05_overlay_stop_confirm_{{expected,actual,diff}}.png"
            )


if __name__ == "__main__":
    unittest.main()
