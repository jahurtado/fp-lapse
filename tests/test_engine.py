"""Tests del motor IDLE/RUNNING. Stdlib unittest, sin sleep real.

Tres piezas de soporte:

- `FakeClock`: monotonic inyectable, avanzado a mano por los tests.
- `StubCamera`: doble de prueba que satisface el `Camera` Protocol; no
  duerme, registra cada `set_params` y `shoot()`, permite inyectar fallos
  y consume "tiempo del fake clock" en cada toma.
- `RecorderBuzzer`: registra cada beep para verificar §6.1.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera import (  # noqa: E402
    CameraBusy,
    CameraInfo,
    CameraNotConnected,
    CameraStatus,
    CaptureFailed,
    CaptureResult,
)
from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import (  # noqa: E402
    ERROR_BUZZER_S,
    Engine,
    EngineError,
    EngineState,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class StubCamera:
    """Doble que cumple el Camera Protocol. No duerme; gasta fake-time."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        shoot_duration_s: float = 0.0,
        connected: bool = True,
    ) -> None:
        self.clock = clock
        self.shoot_duration_s = shoot_duration_s
        self._connected = connected
        self.shots_taken = 0
        self.set_params_calls: List[dict] = []
        self.shoot_calls = 0
        self.injected_failures: List[Exception] = []

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def info(self) -> CameraInfo:
        return CameraInfo(model="STUB", firmware="0", serial="0")

    def status(self) -> CameraStatus:
        return CameraStatus(
            shutter_s=None, aperture=None, iso=None,
            iso_auto=False, exposure_mode=None, focus_mode=None,
        )

    def set_params(self, **kwargs) -> None:
        # Drop None-valued kwargs to match what the engine actually sent.
        self.set_params_calls.append({k: v for k, v in kwargs.items() if v is not None})

    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        self.shoot_calls += 1
        if self.injected_failures:
            err = self.injected_failures.pop(0)
            raise err
        if self.shoot_duration_s:
            self.clock.advance(self.shoot_duration_s)
        self.shots_taken += 1
        return CaptureResult(
            shutter_s=0.0, aperture=0.0, iso=0, duration_s=self.shoot_duration_s
        )


class RecorderBuzzer:
    def __init__(self) -> None:
        self.beeps: List[float] = []

    def beep(self, duration_s: float) -> None:
        self.beeps.append(duration_s)


# --- Helpers ----------------------------------------------------------------


def _single_shot_cfg(name: str = "C", *, interval_s: float = 10.0) -> TimelapseConfig:
    return TimelapseConfig(
        name=name,
        interval_s=interval_s,
        shots=(Shot(shutter=1 / 500, iso=200, aperture=None),),
    )


def _bracket_cfg(
    name: str = "B", *, interval_s: float = 10.0, n: int = 3
) -> TimelapseConfig:
    return TimelapseConfig(
        name=name,
        interval_s=interval_s,
        shots=tuple(Shot(shutter=1 / 500, iso=200) for _ in range(n)),
    )


def _engine(
    *,
    clock: Optional[FakeClock] = None,
    camera: Optional[StubCamera] = None,
    buzzer: Optional[RecorderBuzzer] = None,
):
    clock = clock or FakeClock()
    camera = camera or StubCamera(clock)
    return Engine(camera, buzzer=buzzer, now_monotonic=clock.now), clock, camera


# --- Tests ------------------------------------------------------------------


class TestLifecycle(unittest.TestCase):
    def test_starts_idle(self):
        eng, _, _ = _engine()
        self.assertEqual(eng.state, EngineState.IDLE)
        self.assertIsNone(eng.active_config)
        self.assertIsNone(eng.t0)
        self.assertIsNone(eng.seconds_to_next_shot())

    def test_tick_in_idle_is_noop(self):
        eng, _, cam = _engine()
        eng.tick()
        self.assertEqual(cam.shoot_calls, 0)

    def test_start_transitions_to_running(self):
        eng, clock, _ = _engine()
        eng.start(_single_shot_cfg())
        self.assertEqual(eng.state, EngineState.RUNNING)
        self.assertEqual(eng.t0, clock.now())
        self.assertEqual(eng.active_config.name, "C")

    def test_start_while_running_raises(self):
        eng, _, _ = _engine()
        eng.start(_single_shot_cfg())
        with self.assertRaises(EngineError):
            eng.start(_single_shot_cfg(name="other"))

    def test_stop_returns_to_idle_and_clears(self):
        eng, clock, cam = _engine()
        eng.start(_single_shot_cfg())
        eng.tick()  # fires k=0
        clock.advance(0.5)
        eng.stop()
        self.assertEqual(eng.state, EngineState.IDLE)
        self.assertIsNone(eng.t0)
        self.assertEqual(eng.shots_taken, 0)
        self.assertEqual(eng.skips, 0)

    def test_switch_while_idle_raises(self):
        eng, _, _ = _engine()
        with self.assertRaises(EngineError):
            eng.switch_to(_single_shot_cfg())


class TestFiringGrid(unittest.TestCase):
    def test_first_tick_fires_k_zero_immediately(self):
        eng, _, cam = _engine()
        eng.start(_single_shot_cfg(interval_s=10))
        eng.tick()
        self.assertEqual(cam.shoot_calls, 1)
        self.assertEqual(eng.shots_taken, 1)
        self.assertEqual(eng.skips, 0)

    def test_tick_does_not_fire_before_target(self):
        eng, clock, cam = _engine()
        eng.start(_single_shot_cfg(interval_s=10))
        eng.tick()  # k=0 at t=1000
        clock.advance(3.0)  # t=1003, before k=1 (1010)
        eng.tick()
        self.assertEqual(cam.shoot_calls, 1)

    def test_tick_fires_k1_when_due(self):
        eng, clock, cam = _engine()
        eng.start(_single_shot_cfg(interval_s=10))
        eng.tick()                # k=0
        clock.advance(10.0)       # exactly at k=1
        eng.tick()
        self.assertEqual(cam.shoot_calls, 2)
        self.assertEqual(eng.skips, 0)

    def test_bracket_fires_all_shots_in_one_tick(self):
        eng, _, cam = _engine()
        eng.start(_bracket_cfg(n=5))
        eng.tick()
        self.assertEqual(cam.shoot_calls, 5)
        self.assertEqual(eng.shots_taken, 5)

    def test_seconds_to_next_shot_decreases_with_time(self):
        eng, clock, _ = _engine()
        eng.start(_single_shot_cfg(interval_s=10))
        eng.tick()                # k=0 fired
        clock.advance(2.5)
        self.assertAlmostEqual(eng.seconds_to_next_shot(), 7.5, places=6)

    def test_seconds_to_next_after_late_tick(self):
        # If the loop is delayed past k=1, the next target jumps to k=2 (the
        # SKIP at k=1 is counted) and seconds_to_next reflects time to k=2.
        eng, clock, _ = _engine()
        eng.start(_single_shot_cfg(interval_s=10))
        eng.tick()              # k=0 at t=1000
        clock.advance(15.0)     # t=1015 — k=1 was 5s ago; next target = k=2 at t=1020
        self.assertAlmostEqual(eng.seconds_to_next_shot(), 5.0, places=6)


class TestSkips(unittest.TestCase):
    def test_no_skip_when_bracket_fits_in_interval(self):
        clock = FakeClock()
        cam = StubCamera(clock, shoot_duration_s=2.0)
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=3)  # 3 shots * 2s = 6s < 10s
        eng.start(cfg)
        eng.tick()                      # k=0, ends at t0+6
        clock.advance(4.0)              # t0+10, exactly at k=1
        eng.tick()
        self.assertEqual(eng.shots_taken, 6)
        self.assertEqual(eng.skips, 0)

    def test_skips_counted_when_bracket_outruns_interval(self):
        # Spec example (§5.2): interval 10s, bracket takes 25s → 2 skips.
        clock = FakeClock()
        cam = StubCamera(clock, shoot_duration_s=5.0)
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=5)  # 5*5 = 25s
        eng.start(cfg)
        eng.tick()                      # k=0, bracket ends at t0+25
        eng.tick()                      # sees +2 skips (k=1,k=2) but next target=k=3 at t=t0+30
        self.assertEqual(eng.shots_taken, 5)
        self.assertEqual(eng.skips, 2)
        clock.advance(5.0)              # t=t0+30
        eng.tick()                      # fires k=3
        self.assertEqual(eng.shots_taken, 10)
        self.assertEqual(eng.skips, 2)

    def test_skip_counter_persists_across_subsequent_brackets(self):
        clock = FakeClock()
        cam = StubCamera(clock, shoot_duration_s=5.0)
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=5)
        eng.start(cfg)
        eng.tick()                      # k=0, time -> t0+25
        clock.advance(5.0)              # t0+30
        eng.tick()                      # +2 skips, fires k=3, time -> t0+55
        clock.advance(5.0)              # t0+60
        eng.tick()                      # +2 skips, fires k=6
        self.assertEqual(eng.skips, 4)

    def test_skips_reset_on_stop_then_start(self):
        clock = FakeClock()
        cam = StubCamera(clock, shoot_duration_s=5.0)
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=5)
        eng.start(cfg)
        eng.tick(); eng.tick()          # +2 skips observed
        self.assertEqual(eng.skips, 2)
        eng.stop()
        eng.start(cfg)
        self.assertEqual(eng.skips, 0)

    def test_bracket_with_all_shots_failing_counts_as_skip(self):
        # User-visible scenario: camera unplugged mid-timelapse. The
        # engine still tries each bracket but every shoot raises a
        # `CameraError`. With no images captured for the instant,
        # the grid instant is lost — count as a SKIP so the UI
        # reflects "shots not happening".
        clock = FakeClock()
        cam = StubCamera(clock)
        # Queue 2 failures for the 2 shots in the bracket.
        cam.injected_failures = [
            CameraNotConnected("disconnected"),
            CameraNotConnected("disconnected"),
        ]
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=2)
        eng.start(cfg)
        eng.tick()  # k=0
        self.assertEqual(eng.shots_taken, 0)
        self.assertEqual(eng.skips, 1, "all-failed bracket should count as a SKIP")

    def test_bracket_with_at_least_one_shot_success_is_not_a_skip(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        # First shot fails, second succeeds.
        cam.injected_failures = [CaptureFailed("X")]
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(interval_s=10.0, n=2)
        eng.start(cfg)
        eng.tick()
        self.assertEqual(eng.shots_taken, 1)
        self.assertEqual(eng.skips, 0, "partial-failure bracket is not a skip")


class TestSwitch(unittest.TestCase):
    def test_switch_preserves_t0(self):
        eng, clock, _ = _engine()
        a = _single_shot_cfg("A", interval_s=10.0)
        b = _single_shot_cfg("B", interval_s=5.0)
        eng.start(a)
        t0 = eng.t0
        clock.advance(7.0)
        eng.switch_to(b)
        self.assertEqual(eng.t0, t0)
        self.assertEqual(eng.active_config.name, "B")

    def test_switch_grid_uses_new_period(self):
        # Spec example (§4.2): t0=0, A=10s/2 shots. At t=24s switch to B=5s/3 shots.
        # Next fire on B's grid: smallest t0 + k*5 ≥ 24 → k=5 → t=25.
        clock = FakeClock(start=0.0)
        cam = StubCamera(clock)
        eng = Engine(cam, now_monotonic=clock.now)
        a = TimelapseConfig(
            "A", 10.0,
            (Shot(shutter=1/500, iso=200), Shot(shutter=1/500, iso=200)),
        )
        b = TimelapseConfig(
            "B", 5.0,
            (
                Shot(shutter=1/500, iso=200),
                Shot(shutter=1/500, iso=200),
                Shot(shutter=1/500, iso=200),
            ),
        )
        eng.start(a)
        eng.tick()                  # k=0 of A (2 shots)
        clock.advance(10.0); eng.tick()   # k=1 of A
        clock.advance(10.0); eng.tick()   # k=2 of A
        clock.advance(4.0)                # t=24, mid-interval of A
        eng.switch_to(b)
        # before t=25 nothing fires
        clock.advance(0.5)                # t=24.5
        before = cam.shoot_calls
        eng.tick()
        self.assertEqual(cam.shoot_calls, before)
        clock.advance(0.5)                # t=25
        eng.tick()
        # Three B shots fired at t=25
        self.assertEqual(cam.shoot_calls, before + 3)

    def test_switch_does_not_count_skips_against_new_grid(self):
        # If switch happens mid-interval, the FIRST fire on the new grid
        # should NOT add skips relative to the new k.
        clock = FakeClock(start=0.0)
        cam = StubCamera(clock)
        eng = Engine(cam, now_monotonic=clock.now)
        a = _single_shot_cfg("A", interval_s=30.0)
        b = _single_shot_cfg("B", interval_s=5.0)
        eng.start(a)
        eng.tick()                  # k=0 of A at t=0
        clock.advance(25.0)         # t=25, mid-A-interval
        eng.switch_to(b)            # new grid: k could be 5 (t=25)
        eng.tick()
        # The k=5 fire on B's grid shouldn't be a 4-skip.
        self.assertEqual(eng.skips, 0)

    def test_switch_to_same_config_is_noop(self):
        eng, _, _ = _engine()
        cfg = _single_shot_cfg()
        eng.start(cfg)
        # _last_fired_k stays None (before any fire). Switching to same config
        # should not crash and the state should be unchanged.
        eng.switch_to(cfg)
        self.assertIs(eng.active_config, cfg)


class TestShotTranslation(unittest.TestCase):
    def test_aperture_none_not_sent(self):
        # Aperture is optional (None when the lens is fully manual);
        # shutter and iso are required numerics.
        eng, _, cam = _engine()
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1/500, iso=200, aperture=None),)
        )
        eng.start(cfg); eng.tick()
        call = cam.set_params_calls[-1]
        self.assertIn("shutter_s", call)
        self.assertIn("iso", call)
        self.assertNotIn("aperture", call)

    def test_manual_shot_forces_exposure_manual(self):
        # Each manual shot re-sets exposure_mode=Manual so a physically
        # moved dial mid-session doesn't silently break things.
        from fp_lapse.camera.iface import ExposureMode
        eng, _, cam = _engine()
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1/500, iso=200, aperture=None),)
        )
        eng.start(cfg); eng.tick()
        call = cam.set_params_calls[-1]
        self.assertEqual(call.get("exposure_mode"), ExposureMode.MANUAL)

    def test_auto_config_uses_program_mode_and_fires_one_shot(self):
        # Empty shots tuple → auto mode. Engine sets ExposureMode=Program
        # and shoots once per interval.
        from fp_lapse.camera.iface import ExposureMode
        eng, _, cam = _engine()
        cfg = TimelapseConfig("auto", 10.0, ())
        eng.start(cfg); eng.tick()
        call = cam.set_params_calls[-1]
        self.assertEqual(call.get("exposure_mode"), ExposureMode.PROGRAM)
        self.assertNotIn("shutter_s", call)
        self.assertNotIn("iso", call)
        self.assertEqual(cam.shoot_calls, 1)

    def test_concrete_values_passed(self):
        eng, _, cam = _engine()
        cfg = TimelapseConfig(
            "X", 10.0,
            (Shot(shutter=1/500, iso=400, aperture=5.6),),
        )
        eng.start(cfg); eng.tick()
        call = cam.set_params_calls[-1]
        self.assertAlmostEqual(call["shutter_s"], 1/500)
        self.assertEqual(call["iso"], 400)
        self.assertEqual(call["aperture"], 5.6)


class TestErrorHandling(unittest.TestCase):
    def test_capture_failed_logged_and_not_aborted(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.append(CaptureFailed("AFFailed"))
        buzzer = RecorderBuzzer()
        eng = Engine(cam, buzzer=buzzer, now_monotonic=clock.now)
        cfg = _bracket_cfg(n=3)
        eng.start(cfg)
        eng.tick()  # first shot fails, others succeed
        self.assertEqual(eng.state, EngineState.RUNNING)
        self.assertEqual(eng.shots_taken, 2)
        self.assertEqual(eng.consecutive_failures, 0)  # last 2 succeeded
        self.assertEqual(len(buzzer.beeps), 1)
        self.assertAlmostEqual(buzzer.beeps[0], ERROR_BUZZER_S)

    def test_camera_not_connected_is_caught(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.append(CameraNotConnected("disconnected"))
        eng = Engine(cam, now_monotonic=clock.now)
        eng.start(_single_shot_cfg())
        eng.tick()
        self.assertEqual(eng.state, EngineState.RUNNING)
        self.assertEqual(eng.shots_taken, 0)
        self.assertEqual(eng.consecutive_failures, 1)

    def test_consecutive_failures_accumulate(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.extend(
            [CaptureFailed("F1"), CaptureFailed("F2"), CaptureFailed("F3")]
        )
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(n=3)
        eng.start(cfg)
        eng.tick()
        self.assertEqual(eng.consecutive_failures, 3)
        self.assertEqual(eng.shots_taken, 0)

    def test_success_resets_consecutive_failures(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.extend([CaptureFailed("F1"), CaptureFailed("F2")])
        eng = Engine(cam, now_monotonic=clock.now)
        cfg = _bracket_cfg(n=3)
        eng.start(cfg)
        eng.tick()  # 2 fail, 1 succeed → counter resets at the success
        self.assertEqual(eng.consecutive_failures, 0)
        self.assertEqual(eng.shots_taken, 1)

    def test_busy_treated_as_camera_error(self):
        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.append(CameraBusy("in flight"))
        eng = Engine(cam, now_monotonic=clock.now)
        eng.start(_single_shot_cfg())
        eng.tick()
        self.assertEqual(eng.state, EngineState.RUNNING)
        self.assertEqual(eng.consecutive_failures, 1)

    def test_buzzer_failure_does_not_break_engine(self):
        class BoomBuzzer:
            def beep(self, _: float) -> None:
                raise RuntimeError("buzzer wiring broken")

        clock = FakeClock()
        cam = StubCamera(clock)
        cam.injected_failures.append(CaptureFailed("F"))
        eng = Engine(cam, buzzer=BoomBuzzer(), now_monotonic=clock.now)
        eng.start(_single_shot_cfg())
        eng.tick()  # should not raise
        self.assertEqual(eng.consecutive_failures, 1)


if __name__ == "__main__":
    unittest.main()
