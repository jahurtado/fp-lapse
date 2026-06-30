"""Tests for the TIME SETUP menu (prd2.md §6 — Time Setup menu)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.engine import EngineState  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    MainScreen,
    ScheduleIndicator,
    TimeSetupMenuAction,
    TimeSetupMenuInteraction,
    TimeSetupMenuState,
    UIState,
    render_time_setup_menu,
)
from fp_lapse.ui.time_setup_menu import MENU_ITEMS  # noqa: E402


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


def _green_main_screen() -> Image.Image:
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
        schedule_state=ScheduleIndicator.GREEN,
    ))


def _dump_artifacts(name: str, expected: Image.Image, actual: Image.Image) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    expected.save(ARTIFACTS_DIR / f"{name}_expected.png")
    actual.save(ARTIFACTS_DIR / f"{name}_actual.png")
    return ARTIFACTS_DIR


class TestTimeSetupMenuConstants(unittest.TestCase):
    def test_menu_items_fixed(self):
        self.assertEqual(
            MENU_ITEMS,
            ("Sync Time (NTP)", "Set Time (Manual)", "Wi-Fi setup"),
        )

    def test_actions_has_four_members(self):
        members = set(TimeSetupMenuAction)
        self.assertEqual(
            members,
            {
                TimeSetupMenuAction.FORCE_NTP_SYNC,
                TimeSetupMenuAction.SET_MANUALLY,
                TimeSetupMenuAction.WIFI_SETUP,
                TimeSetupMenuAction.CANCEL,
            },
        )

    def test_ok_on_third_returns_wifi_setup(self):
        ix = TimeSetupMenuInteraction()
        ix.cursor = 2
        self.assertEqual(
            ix.on_press(ButtonId.OK),
            TimeSetupMenuAction.WIFI_SETUP,
        )


class TestTimeSetupInteraction(unittest.TestCase):
    def test_starts_at_first_item(self):
        ix = TimeSetupMenuInteraction()
        self.assertEqual(ix.cursor, 0)

    def test_up_clamps_at_first(self):
        ix = TimeSetupMenuInteraction()
        ix.on_press(ButtonId.UP)
        self.assertEqual(ix.cursor, 0)

    def test_down_clamps_at_last(self):
        ix = TimeSetupMenuInteraction()
        for _ in range(5):
            ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.cursor, len(MENU_ITEMS) - 1)

    def test_ok_on_first_returns_force_ntp_sync(self):
        ix = TimeSetupMenuInteraction()
        self.assertEqual(
            ix.on_press(ButtonId.OK),
            TimeSetupMenuAction.FORCE_NTP_SYNC,
        )

    def test_ok_on_second_returns_set_manually(self):
        ix = TimeSetupMenuInteraction()
        ix.cursor = 1
        self.assertEqual(
            ix.on_press(ButtonId.OK),
            TimeSetupMenuAction.SET_MANUALLY,
        )

    def test_back_returns_cancel_regardless_of_cursor(self):
        for c in range(len(MENU_ITEMS)):
            with self.subTest(cursor=c):
                ix = TimeSetupMenuInteraction()
                ix.cursor = c
                self.assertEqual(
                    ix.on_press(ButtonId.BACK),
                    TimeSetupMenuAction.CANCEL,
                )

    def test_left_right_have_no_effect(self):
        ix = TimeSetupMenuInteraction()
        ix.cursor = 1
        self.assertIsNone(ix.on_press(ButtonId.LEFT))
        self.assertIsNone(ix.on_press(ButtonId.RIGHT))
        self.assertEqual(ix.cursor, 1)

    def test_reset_returns_to_first(self):
        ix = TimeSetupMenuInteraction()
        ix.cursor = 1
        ix.reset()
        self.assertEqual(ix.cursor, 0)


class TestRenderTimeSetupMenuSmoke(unittest.TestCase):
    def test_renders_320x240(self):
        out = render_time_setup_menu(
            _green_main_screen(), TimeSetupMenuState(cursor=0),
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_rejects_wrong_base_size(self):
        wrong = Image.new("RGB", (100, 100), (0, 0, 0))
        with self.assertRaises(ValueError):
            render_time_setup_menu(wrong, TimeSetupMenuState(cursor=0))

    def test_rejects_cursor_out_of_range(self):
        with self.assertRaises(ValueError):
            render_time_setup_menu(
                _green_main_screen(), TimeSetupMenuState(cursor=len(MENU_ITEMS)),
            )

    def test_each_cursor_renders_distinct(self):
        base = _green_main_screen()
        outs = [
            render_time_setup_menu(base, TimeSetupMenuState(cursor=i)).tobytes()
            for i in range(len(MENU_ITEMS))
        ]
        self.assertEqual(len(set(outs)), len(MENU_ITEMS))


class TestTimeSetupMenuSyncing(unittest.TestCase):
    """Addendum A1: animated `Syncing<dots>` while the worker runs."""

    def test_syncing_dots_changes_pixels(self):
        """Each of the three animation phases produces a distinct image
        from idle and from each other."""
        base = _green_main_screen()
        idle = render_time_setup_menu(
            base, TimeSetupMenuState(cursor=0),
        ).tobytes()
        renders = []
        for n in (1, 2, 3):
            img = render_time_setup_menu(
                base, TimeSetupMenuState(cursor=0, syncing_dots=n),
            ).tobytes()
            self.assertNotEqual(img, idle, f"syncing_dots={n} same as idle")
            renders.append(img)
        self.assertEqual(
            len(set(renders)), 3,
            "the three dot phases must each render distinctly",
        )

    def test_syncing_locks_highlight_to_item_zero(self):
        """During sync, even cursor=1 must render with the highlight on
        item 0 — the worker owns the menu until it finishes."""
        base = _green_main_screen()
        cursor_zero_syncing = render_time_setup_menu(
            base, TimeSetupMenuState(cursor=0, syncing_dots=2),
        ).tobytes()
        cursor_one_syncing = render_time_setup_menu(
            base, TimeSetupMenuState(cursor=1, syncing_dots=2),
        ).tobytes()
        self.assertEqual(cursor_zero_syncing, cursor_one_syncing)

    def test_invalid_dots_value_raises(self):
        base = _green_main_screen()
        for bad in (0, 4, -1, 10):
            with self.assertRaises(ValueError):
                render_time_setup_menu(
                    base, TimeSetupMenuState(cursor=0, syncing_dots=bad),
                )


class TestTimeSetupMenuVisualRegression(unittest.TestCase):
    def test_14_main_idle_time_setup_menu(self):
        expected_path = MOCKUPS_DIR / "14_main_idle_time_setup_menu.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        actual = render_time_setup_menu(
            _green_main_screen(), TimeSetupMenuState(cursor=0),
        )
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts(
                "14_main_idle_time_setup_menu", expected, actual,
            )
            self.fail(
                f"14_main_idle_time_setup_menu.png differs from production "
                f"render — see {out}/14_main_idle_time_setup_menu_"
                f"{{expected,actual}}.png"
            )

    def test_20_settings_menu(self):
        expected_path = MOCKUPS_DIR / "20_settings_menu.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        actual = render_time_setup_menu(
            _green_main_screen(), TimeSetupMenuState(cursor=0),
        )
        self.assertEqual(actual.tobytes(), expected.tobytes())


if __name__ == "__main__":
    unittest.main()
