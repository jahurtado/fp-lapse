"""Tests for the manage menu modal.

Smoke + pixel-exact visual regression against `06_manage_menu.png`.
The base is rebuilt with `MainScreen` in IDLE (same fixtures as
`test_ui_main_screen.py`).
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
    MENU_ITEMS,
    MainScreen,
    ManageMenuState,
    UIState,
    render_manage_menu,
)


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
        Shot(shutter=2.0,     iso=1600, aperture=None),
    ),
)
FREE = TimelapseConfig(name="Free daytime", interval_s=30.0, shots=())


def _idle_main_screen() -> Image.Image:
    """Mismo base que el mockup 01 / 06 (cursor en Partial, IDLE)."""
    return MainScreen().render(UIState(
        configs=(PARTIAL, TOTALITY, FREE),
        cursor=0,
        engine_state=EngineState.IDLE,
        active_config_name=None,
        shots_taken=0,
        seconds_to_next_shot=None,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
    ))


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


class TestManageMenuConstants(unittest.TestCase):
    def test_menu_items_fixed_order(self):
        self.assertEqual(MENU_ITEMS, ("Edit", "Duplicate", "Delete", "Cancel"))


class TestRenderManageMenuSmoke(unittest.TestCase):
    def test_renders_320x240_rgb(self):
        base = _idle_main_screen()
        out = render_manage_menu(
            base, ManageMenuState(config_name="Totality", cursor=0),
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_rejects_wrong_base_size(self):
        wrong = Image.new("RGB", (100, 100), (0, 0, 0))
        with self.assertRaises(ValueError):
            render_manage_menu(wrong, ManageMenuState("X", 0))

    def test_rejects_cursor_out_of_range(self):
        base = _idle_main_screen()
        with self.assertRaises(ValueError):
            render_manage_menu(base, ManageMenuState("X", len(MENU_ITEMS)))
        with self.assertRaises(ValueError):
            render_manage_menu(base, ManageMenuState("X", -1))

    def test_each_cursor_position_renders_distinct_bytes(self):
        base = _idle_main_screen()
        outs = [
            render_manage_menu(base, ManageMenuState("X", i)).tobytes()
            for i in range(len(MENU_ITEMS))
        ]
        self.assertEqual(len(set(outs)), len(MENU_ITEMS))

    def test_overlay_modifies_base(self):
        base = _idle_main_screen()
        out = render_manage_menu(base, ManageMenuState("Totality", 0))
        self.assertNotEqual(base.tobytes(), out.tobytes())


class TestManageMenuVisualRegression(unittest.TestCase):
    """Pixel-exact contra `docs/mockups/06_manage_menu.png`."""

    def test_06_manage_menu(self):
        base = _idle_main_screen()
        actual = render_manage_menu(
            base, ManageMenuState(config_name="Totality", cursor=0),
        )
        expected_path = MOCKUPS_DIR / "06_manage_menu.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("06_manage_menu", expected, actual)
            self.fail(
                f"06_manage_menu.png differs from production render — "
                f"see {out}/06_manage_menu_{{expected,actual,diff}}.png"
            )


if __name__ == "__main__":
    unittest.main()
