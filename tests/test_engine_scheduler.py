"""Tests for `EngineScheduler` — the engine's dedicated thread.

These exercise the real `threading.Event`/`Thread` machinery with
short timeouts. Each test boots a fresh scheduler against a stub
camera, sends commands from the main thread, and asserts the
observable side-effects (shots taken, dirty events fired, timing).

Engine-internal logic (skip counting, catch-up, etc.) is already
covered by `test_engine.py` against `engine.tick()` directly; here we
only verify that the scheduler calls `tick()` at the right moments
and forwards commands correctly.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera import (  # noqa: E402
    CameraInfo,
    CameraStatus,
    CaptureResult,
)
from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import Engine, EngineState  # noqa: E402
from fp_lapse.engine_scheduler import EngineScheduler  # noqa: E402


class FastCamera:
    """Minimal Camera Protocol stub. shoot() returns immediately."""

    def __init__(self) -> None:
        self.shoot_calls = 0
        self.set_params_calls: List[dict] = []

    def connect(self) -> None: pass
    def disconnect(self) -> None: pass
    def is_connected(self) -> bool: return True
    def info(self) -> CameraInfo: return CameraInfo("STUB", "0", "0")
    def status(self) -> CameraStatus:
        return CameraStatus(None, None, None, False, None, None, None, None)

    def set_params(self, **kwargs) -> None:
        self.set_params_calls.append(kwargs)

    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        self.shoot_calls += 1
        return CaptureResult(0.0, 0.0, 0, 0.0)


def _cfg(name: str = "C", interval_s: float = 0.2, n_shots: int = 1) -> TimelapseConfig:
    return TimelapseConfig(
        name=name,
        interval_s=interval_s,
        shots=tuple(Shot(shutter=0.001, iso=100, aperture=None) for _ in range(n_shots)),
    )


class TestEngineSchedulerLifecycle(unittest.TestCase):
    def test_idle_thread_blocks_until_cmd_start(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            # Give the thread a moment to enter the idle wait.
            time.sleep(0.05)
            self.assertEqual(engine.state, EngineState.IDLE)
            self.assertEqual(cam.shoot_calls, 0)

            sched.cmd_start(_cfg(interval_s=10.0, n_shots=1))
            # First fire is ASAP (k=0). Wait briefly for the thread to
            # pick up the command and fire.
            for _ in range(50):
                if cam.shoot_calls >= 1:
                    break
                time.sleep(0.01)
            self.assertEqual(cam.shoot_calls, 1)
            self.assertEqual(engine.state, EngineState.RUNNING)
        finally:
            sched.shutdown()

    def test_shutdown_is_clean(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        sched.shutdown(timeout=2.0)
        self.assertFalse(sched._thread.is_alive())

    def test_dirty_event_fires_on_start(self):
        cam = FastCamera()
        engine = Engine(cam)
        dirty = threading.Event()
        sched = EngineScheduler(engine, dirty_event=dirty)
        sched.start()
        try:
            sched.cmd_start(_cfg(interval_s=10.0))
            self.assertTrue(dirty.wait(timeout=0.5))
        finally:
            sched.shutdown()


class TestEngineSchedulerFiring(unittest.TestCase):
    def test_fires_at_each_grid_mark(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            # interval = 100 ms, 1 shot per bracket — over 350 ms we
            # expect k=0, k=1, k=2, k=3 = 4 shots.
            sched.cmd_start(_cfg(interval_s=0.1, n_shots=1))
            time.sleep(0.42)
            self.assertGreaterEqual(cam.shoot_calls, 4)
            self.assertLessEqual(cam.shoot_calls, 6)
        finally:
            sched.shutdown()

    def test_stop_halts_firing(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start(_cfg(interval_s=0.1, n_shots=1))
            time.sleep(0.15)  # k=0 + k=1 likely fired
            before = cam.shoot_calls
            sched.cmd_stop()
            time.sleep(0.3)
            self.assertEqual(cam.shoot_calls, before)
            self.assertEqual(engine.state, EngineState.IDLE)
        finally:
            sched.shutdown()

    def test_switch_changes_period(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start(_cfg(name="slow", interval_s=1.0, n_shots=1))
            time.sleep(0.05)  # k=0 fires ASAP
            self.assertEqual(cam.shoot_calls, 1)

            # Switch to a tight grid; in the next 150 ms we should see
            # several more fires on the new period.
            sched.cmd_switch(_cfg(name="fast", interval_s=0.05, n_shots=1))
            time.sleep(0.18)
            self.assertGreaterEqual(cam.shoot_calls, 3)
        finally:
            sched.shutdown()

    def test_cmd_start_while_running_is_logged_not_raised(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start(_cfg(interval_s=10.0))
            # Calling start again should not propagate the EngineError.
            sched.cmd_start(_cfg(name="other", interval_s=10.0))
            # Engine still on original config — the second start was
            # rejected and swallowed.
            time.sleep(0.05)
            self.assertEqual(engine.active_config.name, "C")
        finally:
            sched.shutdown()


class TestEngineSchedulerStatusReads(unittest.TestCase):
    def test_engine_property_exposes_underlying(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        self.assertIs(sched.engine, engine)


if __name__ == "__main__":
    unittest.main()
