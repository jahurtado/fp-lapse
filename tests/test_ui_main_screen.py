"""Tests for the main screen (IDLE + RUNNING).

Doble objetivo:

1. **Smoke**: the screen renders a valid 320x240 RGB image for each
   significant state combination (IDLE, RUNNING / cursor on running,
   RUNNING / cursor elsewhere).
2. **Visual regression**: the result matches byte-for-byte the
   approved mockups in `docs/mockups/`. On failure both artifacts
   (expected and actual) are dumped to `runtime/test_artifacts/` for
   manual inspection (`open *.png`).

Important: the mockups were generated with Menlo (macOS). On the Pi
the UI uses DejaVuSansMono → pixels won't be identical. These tests
are meant to run on the Mac dev box; on the Pi only the smoke ones
should pass.
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
from fp_lapse.ui import MainScreen, ScheduleIndicator, UIState  # noqa: E402
from fp_lapse.ui.main_screen import footer_hint  # noqa: E402


# Fixture data — tiene que casar 1:1 con lo que `docs/mockups/render_mockups.py`
# usa al generar los PNGs.

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

# Auto mode — 1 shot per interval, camera meters.
FREE = TimelapseConfig(name="Free daytime", interval_s=30.0, shots=())

MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "test_artifacts"


def _dump_artifacts(name: str, expected: Image.Image, actual: Image.Image) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    expected.save(ARTIFACTS_DIR / f"{name}_expected.png")
    actual.save(ARTIFACTS_DIR / f"{name}_actual.png")
    # Highlight diffs in magenta for quick visual scan.
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


class TestMainScreenSmoke(unittest.TestCase):
    def _state_idle(self) -> UIState:
        return UIState(
            configs=(PARTIAL, TOTALITY, FREE),
            cursor=0,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )

    def test_idle_renders_320x240_rgb(self):
        img = MainScreen().render(self._state_idle())
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")

    def test_running_cursor_on_running(self):
        st = UIState(
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
        img = MainScreen().render(st)
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_running_cursor_elsewhere(self):
        st = UIState(
            configs=(TOTALITY, PARTIAL),
            cursor=1,
            engine_state=EngineState.RUNNING,
            active_config_name="Totality",
            shots_taken=142,
            seconds_to_next_shot=4.3,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )
        img = MainScreen().render(st)
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_cursor_on_new_idle(self):
        st = UIState(
            configs=(PARTIAL,),
            cursor=1,                  # past the only config → "+ New"
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )
        img = MainScreen().render(st)
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_camera_banner_pixels_differ_from_no_banner(self):
        from dataclasses import replace
        base = UIState(
            configs=(PARTIAL,),
            cursor=0,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=False,
            wall_clock_str="18:42:07",
        )
        plain = MainScreen().render(base).tobytes()
        with_banner = MainScreen().render(
            replace(base, camera_not_responding=True)
        ).tobytes()
        self.assertNotEqual(plain, with_banner)

    def test_configs_reset_banner_pixels_differ(self):
        from dataclasses import replace
        base = UIState(
            configs=(),
            cursor=0,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )
        plain = MainScreen().render(base).tobytes()
        with_banner = MainScreen().render(
            replace(base, configs_reset=True)
        ).tobytes()
        self.assertNotEqual(plain, with_banner)


class TestMainScreenVisualRegression(unittest.TestCase):
    """Pixel-exact match contra `docs/mockups/*.png`. Mac-only."""

    def _assert_matches(self, state: UIState, mockup_name: str) -> None:
        expected_path = MOCKUPS_DIR / f"{mockup_name}.png"
        self.assertTrue(
            expected_path.exists(),
            f"reference mockup missing: {expected_path}",
        )
        expected = Image.open(expected_path).convert("RGB")
        actual = MainScreen().render(state)
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts(mockup_name, expected, actual)
            self.fail(
                f"{mockup_name}.png differs from production render — "
                f"see {out}/{mockup_name}_{{expected,actual,diff}}.png"
            )

    def test_01_main_idle(self):
        self._assert_matches(
            UIState(
                configs=(PARTIAL, TOTALITY, FREE),
                cursor=0,
                engine_state=EngineState.IDLE,
                active_config_name=None,
                shots_taken=0,
                seconds_to_next_shot=None,
                skips=0,
                camera_connected=True,
                wall_clock_str="18:42:07",
            ),
            "01_main_idle",
        )

    def test_02_running_cursor_on_running(self):
        self._assert_matches(
            UIState(
                configs=(TOTALITY, PARTIAL),
                cursor=0,
                engine_state=EngineState.RUNNING,
                active_config_name="Totality",
                shots_taken=142,
                seconds_to_next_shot=4.3,
                skips=0,
                camera_connected=True,
                wall_clock_str="18:42:07",
            ),
            "02_main_running_cursor_on_running",
        )

    def test_03_running_cursor_elsewhere(self):
        self._assert_matches(
            UIState(
                configs=(TOTALITY, PARTIAL),
                cursor=1,
                engine_state=EngineState.RUNNING,
                active_config_name="Totality",
                shots_taken=142,
                seconds_to_next_shot=4.3,
                skips=0,
                camera_connected=True,
                wall_clock_str="18:42:07",
            ),
            "03_main_running_cursor_elsewhere",
        )


class TestFooterHintTwoLines(unittest.TestCase):
    """Two-line footer (§7.8 addendum): `footer_hint` returns
    `(primary, secondary)`. Primary depends on state (OK/BACK/hold OK
    actions). Secondary is constant — it carries the global shortcuts
    (`← time setup`, `→ sched on/off`, `OK+BACK shutdown`) so they
    stay discoverable in every state without crowding the primary
    line."""

    def _idle_state(self, cursor: int) -> UIState:
        return UIState(
            configs=(PARTIAL,),
            cursor=cursor,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )

    def _running_state(self, cursor: int) -> UIState:
        return UIState(
            configs=(PARTIAL, TOTALITY),
            cursor=cursor,
            engine_state=EngineState.RUNNING,
            active_config_name="Partial",
            shots_taken=10,
            seconds_to_next_shot=4.3,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
        )

    # --- secondary is the same constant in every state ---

    def _assert_secondary_complete(self, secondary: str) -> None:
        self.assertIn("← time setup", secondary)
        self.assertIn("→ sched on/off", secondary)
        self.assertIn("OK+ESC shutdown", secondary)
        # No leftover from the old single-line format.
        self.assertNotIn("→ sched", secondary.replace("→ sched on/off", ""))
        self.assertNotIn("trust", secondary.lower())

    def test_secondary_constant_in_idle_on_config(self):
        _, secondary = footer_hint(self._idle_state(cursor=0))
        self._assert_secondary_complete(secondary)

    def test_secondary_constant_in_idle_on_new(self):
        _, secondary = footer_hint(self._idle_state(cursor=1))
        self._assert_secondary_complete(secondary)

    def test_secondary_constant_in_running_on_running(self):
        _, secondary = footer_hint(self._running_state(cursor=0))
        self._assert_secondary_complete(secondary)

    def test_secondary_constant_in_running_off_running(self):
        _, secondary = footer_hint(self._running_state(cursor=1))
        self._assert_secondary_complete(secondary)

    # --- primary is state-dependent and free of LEFT/RIGHT noise ---

    def test_idle_on_config_keeps_hold_menu_hint(self):
        """Addendum C survives the two-line refactor: the IDLE on
        real-config primary keeps the `hold OK menu` discoverability,
        and no longer competes with `← time → sched` for width."""
        primary, _ = footer_hint(self._idle_state(cursor=0))
        self.assertIn("OK run", primary)
        self.assertIn("hold OK menu", primary)
        # Secondary line carries those — primary must not duplicate.
        self.assertNotIn("← time", primary)
        self.assertNotIn("→ sched", primary)

    def test_idle_on_new_primary_is_clean(self):
        primary, _ = footer_hint(self._idle_state(cursor=1))
        self.assertIn("OK new", primary)
        self.assertNotIn("← time", primary)
        self.assertNotIn("→ sched", primary)

    def test_running_on_running_primary_is_clean(self):
        primary, _ = footer_hint(self._running_state(cursor=0))
        self.assertIn("ESC stop", primary)
        self.assertNotIn("← time", primary)
        self.assertNotIn("→ sched", primary)

    def test_running_off_running_primary_has_switch(self):
        primary, _ = footer_hint(self._running_state(cursor=1))
        self.assertIn("OK switch", primary)
        self.assertIn("ESC stop", primary)
        self.assertNotIn("← time", primary)


class TestScheduleIndicatorRegression(unittest.TestCase):
    """Pixel-exact byte equality against the 07..10 + 19 mockups."""

    def _state(
        self, ind: ScheduleIndicator, *, disabled: bool = False,
    ) -> UIState:
        return UIState(
            configs=(PARTIAL, TOTALITY, FREE),
            cursor=0,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="18:42:07",
            schedule_state=ind,
            schedule_disabled=disabled,
        )

    def _check(
        self, name: str, ind: ScheduleIndicator, *, disabled: bool = False,
    ) -> None:
        expected_path = MOCKUPS_DIR / f"{name}.png"
        self.assertTrue(expected_path.exists(), f"missing {expected_path}")
        expected = Image.open(expected_path).convert("RGB")
        actual = MainScreen().render(self._state(ind, disabled=disabled))
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts(name, expected, actual)
            self.fail(
                f"{name}.png differs from production render — see {out}/{name}_*.png"
            )

    def test_07_off(self):
        self._check("07_main_idle_schedule_off", ScheduleIndicator.OFF)

    def test_08_red(self):
        self._check("08_main_idle_schedule_red", ScheduleIndicator.RED)

    def test_09_green(self):
        self._check("09_main_idle_schedule_green", ScheduleIndicator.GREEN)

    def test_10_yellow(self):
        self._check("10_main_idle_schedule_yellow", ScheduleIndicator.YELLOW)

    def test_19_disabled_with_strikethrough(self):
        # §6 addendum: when scheduling is disabled, the would-be
        # color still renders but the clock gets a diagonal
        # strikethrough. Baseline uses GREEN here — operator turned
        # scheduling off after a successful first sync.
        self._check(
            "19_main_idle_schedule_disabled",
            ScheduleIndicator.GREEN,
            disabled=True,
        )


class TestComputeScrollOffset(unittest.TestCase):
    """Addendum I: stateless auto-scroll for the main-screen config list.
    `_compute_scroll_offset(cursor, heights, visible_h)` returns the
    smallest start index s.t. the cursor's block fits inside `visible_h`.
    """

    def _compute(self, *args, **kwargs):
        from fp_lapse.ui.main_screen import _compute_scroll_offset
        return _compute_scroll_offset(*args, **kwargs)

    def test_no_scroll_when_everything_fits(self):
        # Two small blocks + +New all fit in 200 px.
        self.assertEqual(
            self._compute(cursor=0, heights=[50, 50, 16], visible_h=200),
            0,
        )
        self.assertEqual(
            self._compute(cursor=2, heights=[50, 50, 16], visible_h=200),
            0,
        )

    def test_scrolls_when_cursor_block_overflows(self):
        # Three 80-px blocks + 16-px +New = 256 px, visible_h=200.
        # Cursor on last config (index 2). Need to scroll past block 0
        # so blocks 1..2 = 160 px fit.
        self.assertEqual(
            self._compute(cursor=2, heights=[80, 80, 80, 16], visible_h=200),
            1,
        )

    def test_cursor_on_new_scrolls_to_show_it(self):
        # Cursor on +New (index 3). Need start s.t. sum(heights[start:4]) <= 200.
        # heights[1:4] = 80+80+16 = 176, fits. heights[0:4] = 256, no.
        self.assertEqual(
            self._compute(cursor=3, heights=[80, 80, 80, 16], visible_h=200),
            1,
        )

    def test_scrolls_back_when_cursor_moves_up(self):
        # Cursor on block 0 — start always 0.
        self.assertEqual(
            self._compute(cursor=0, heights=[80, 80, 80, 16], visible_h=200),
            0,
        )

    def test_degenerate_block_larger_than_visible_clamps_to_cursor(self):
        # A single block taller than the whole visible area — clamp to
        # the cursor so the cursor's block at least renders from the top
        # of the visible area (partial render below the footer line).
        self.assertEqual(
            self._compute(cursor=1, heights=[50, 250, 50], visible_h=200),
            1,
        )


class TestMainScreenScrollIntegration(unittest.TestCase):
    """Auto-scroll exercised through the public `MainScreen.render`."""

    def _state(self, cursor: int, configs: tuple) -> UIState:
        return UIState(
            configs=configs,
            cursor=cursor,
            engine_state=EngineState.IDLE,
            active_config_name=None,
            shots_taken=0,
            seconds_to_next_shot=None,
            skips=0,
            camera_connected=True,
            wall_clock_str="00:47:55",
        )

    def _three_tall_configs(self):
        from fp_lapse.schedule.moment import ScheduledMoment
        from datetime import time as _time, date as _date
        return (
            TimelapseConfig(
                "Noche", 30.0,
                shots=(
                    Shot(shutter=5.0, iso=100, aperture=2.8),
                    Shot(shutter=1.0, iso=100, aperture=2.8),
                    Shot(shutter=1/100, iso=100, aperture=2.8),
                ),
                start=ScheduledMoment(
                    time=_time(4, 30, 0), date=_date(2026, 5, 30),
                ),
            ),
            TimelapseConfig(
                "Crepúsculo", 30.0,
                shots=tuple(
                    Shot(shutter=s, iso=100, aperture=8.0)
                    for s in (5.0, 0.25, 1/60, 1/1000, 1/4000)
                ),
                start=ScheduledMoment(
                    time=_time(6, 0, 0), date=_date(2026, 5, 30),
                ),
            ),
            TimelapseConfig(
                "Sol", 30.0,
                shots=tuple(
                    Shot(shutter=s, iso=100, aperture=22.0)
                    for s in (1/8, 1/30, 1/200, 1/1000, 1/4000)
                ),
                start=ScheduledMoment(
                    time=_time(6, 55, 0), date=_date(2026, 5, 30),
                ),
                end=ScheduledMoment(
                    time=_time(8, 30, 0), date=_date(2026, 5, 30),
                ),
            ),
        )

    def test_cursor_on_overflowing_last_config_renders_different_from_cursor_on_first(self):
        """If the three configs don't all fit, the render with cursor on
        the last must differ from the render with cursor on the first —
        the auto-scroll has hidden the first block."""
        configs = self._three_tall_configs()
        top = MainScreen().render(self._state(0, configs)).tobytes()
        bottom = MainScreen().render(self._state(2, configs)).tobytes()
        self.assertNotEqual(top, bottom)

    def test_render_does_not_raise_for_long_config_list(self):
        """The render path tolerates a list whose total height exceeds
        the visible area without crashing."""
        configs = self._three_tall_configs() * 3   # 9 configs — definitely overflows
        # cursor anywhere in 0..len-1 must produce a valid 320×240 image.
        for c in (0, len(configs) - 1, len(configs)):  # incl. + New
            img = MainScreen().render(self._state(c, configs))
            self.assertEqual(img.size, (WIDTH, HEIGHT))


if __name__ == "__main__":
    unittest.main()
