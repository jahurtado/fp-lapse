"""Visual regression for the safe-shutdown screens (§7.8).

Two frames live in `docs/mockups/`:

- `16_overlay_poweroff.png` — `Power off?` modal over the idle main.
- `17_powering_off.png`     — green `POWERING OFF…` + LED hint, the
  single screen shown from the moment OK confirms until the operator
  unplugs (kernel halt → TFT panel memory retains the frame).

Any pixel diff vs the production render fails the test and dumps the
expected/actual/diff PNGs to `runtime/test_artifacts/`. Same pattern
as `test_ui_overlays.py`. After an intentional UI change, regenerate
the mockups with `docs/mockups/render_mockups.py` and commit the new
PNGs alongside the code change.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    poweroff_confirm,
    render_overlay,
    render_powering_off,
)


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "test_artifacts"


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


def _render_main_idle_base() -> Image.Image:
    """Replicates the `render_main_idle()` base used by mockup 16
    (overlay sits on top of `01_main_idle.png` content).
    """
    from datetime import date as _date_t, time as _time_t
    from fp_lapse.configs import Shot, TimelapseConfig
    from fp_lapse.engine import EngineState
    from fp_lapse.schedule.moment import ScheduledMoment
    from fp_lapse.ui import MainScreen, UIState

    partial = TimelapseConfig(
        name="Partial", interval_s=10.0,
        shots=(Shot(shutter=1 / 1000, iso=200, aperture=None),),
    )
    eclipse_day = _date_t(2026, 8, 12)
    tot = TimelapseConfig(
        name="Totality", interval_s=5.0,
        shots=(
            Shot(shutter=1 / 500, iso=400, aperture=None),
            Shot(shutter=1 / 30, iso=400, aperture=None),
        ),
        start=ScheduledMoment(time=_time_t(11, 33, 23), date=eclipse_day),
        end=ScheduledMoment(time=_time_t(11, 36, 9), date=eclipse_day),
    )
    daily = TimelapseConfig(
        name="Sunrise loop", interval_s=30.0, shots=(),
        start=ScheduledMoment(time=_time_t(7, 0, 0)),
        end=ScheduledMoment(time=_time_t(19, 0, 0)),
    )
    from fp_lapse.ui.schedule_indicator import ScheduleIndicator
    return MainScreen().render(UIState(
        configs=(partial, tot, daily),
        cursor=1,
        engine_state=EngineState.IDLE,
        active_config_name=None,
        shots_taken=0,
        seconds_to_next_shot=None,
        skips=0,
        camera_connected=True,
        wall_clock_str="09:55:12",
        schedule_state=ScheduleIndicator.GREEN,
    ))


class TestPoweringOffSmoke(unittest.TestCase):
    def test_returns_320x240_rgb(self):
        img = render_powering_off()
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")


class TestShutdownVisualRegression(unittest.TestCase):

    def _assert_matches(self, name: str, actual: Image.Image) -> None:
        expected_path = MOCKUPS_DIR / f"{name}.png"
        self.assertTrue(expected_path.exists(),
                        f"missing mockup {expected_path}")
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts(name, expected, actual)
            self.fail(
                f"{name}.png differs from production render — "
                f"see {out}/{name}_{{expected,actual,diff}}.png"
            )

    def test_16_overlay_poweroff(self):
        actual = render_overlay(_render_main_idle_base(), poweroff_confirm())
        self._assert_matches("16_overlay_poweroff", actual)

    def test_17_powering_off(self):
        self._assert_matches("17_powering_off", render_powering_off())


if __name__ == "__main__":
    unittest.main()
