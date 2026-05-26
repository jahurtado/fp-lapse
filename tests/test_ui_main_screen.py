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
from fp_lapse.ui import MainScreen, UIState  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
