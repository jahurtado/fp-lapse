"""Tests for the `cmd_*_async` mailbox extension on `EngineScheduler`.

Single-slot mailbox + last-write-wins. The async path enqueues a
closure that the scheduler thread picks up at the top of its next
loop iteration and applies via the same `_cmd_lock`-guarded path as
the blocking `cmd_*` methods.
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


def _cfg(name: str = "C", interval_s: float = 10.0, n_shots: int = 1) -> TimelapseConfig:
    return TimelapseConfig(
        name=name,
        interval_s=interval_s,
        shots=tuple(Shot(shutter=0.001, iso=100, aperture=None) for _ in range(n_shots)),
    )


class TestAsyncCommandsNonBlocking(unittest.TestCase):
    """The async sibling must NOT block on `_cmd_lock`."""

    def test_async_caller_does_not_block_when_cmd_lock_is_held(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        # Don't start the scheduler thread — we want to verify the
        # async caller returns immediately even when `_cmd_lock` is
        # currently held by another party.
        held = threading.Event()
        release = threading.Event()

        def holder():
            with sched._cmd_lock:
                held.set()
                release.wait(timeout=5.0)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        self.assertTrue(held.wait(timeout=1.0))

        # The async call must NOT block on `_cmd_lock`.
        start = time.monotonic()
        sched.cmd_start_async(_cfg())
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1, "cmd_start_async blocked on _cmd_lock")

        release.set()
        t.join(timeout=2.0)

    def test_cmd_stop_async_returns_immediately(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        held = threading.Event()
        release = threading.Event()

        def holder():
            with sched._cmd_lock:
                held.set()
                release.wait(timeout=5.0)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        self.assertTrue(held.wait(timeout=1.0))

        start = time.monotonic()
        sched.cmd_stop_async()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)
        release.set()
        t.join(timeout=2.0)

    def test_cmd_switch_async_returns_immediately(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        held = threading.Event()
        release = threading.Event()

        def holder():
            with sched._cmd_lock:
                held.set()
                release.wait(timeout=5.0)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        self.assertTrue(held.wait(timeout=1.0))

        start = time.monotonic()
        sched.cmd_switch_async(_cfg(name="other"))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)
        release.set()
        t.join(timeout=2.0)


class TestMailboxLastWriteWins(unittest.TestCase):
    def test_second_async_overrides_first_pending(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        # Don't start the thread. Enqueue two async commands and inspect
        # the mailbox.
        sched.cmd_start_async(_cfg(name="first"))
        sched.cmd_start_async(_cfg(name="second"))
        # Mailbox holds the second closure (the first was overwritten).
        self.assertIsNotNone(sched._pending_cmd)
        # Invoke it manually and observe that the engine ends up running
        # the SECOND config.
        sched._pending_cmd()
        self.assertEqual(engine.active_config.name, "second")


class TestMailboxAppliedByThread(unittest.TestCase):
    def test_thread_picks_up_pending_cmd_at_top_of_loop(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            self.assertEqual(engine.state, EngineState.IDLE)
            sched.cmd_start_async(_cfg(interval_s=10.0))
            # Wait for the engine to actually fire k=0.
            for _ in range(100):
                if cam.shoot_calls >= 1:
                    break
                time.sleep(0.01)
            self.assertEqual(cam.shoot_calls, 1)
            self.assertEqual(engine.state, EngineState.RUNNING)
            # Mailbox cleared after pickup.
            self.assertIsNone(sched._pending_cmd)
        finally:
            sched.shutdown()

    def test_async_stop_works(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start_async(_cfg(interval_s=0.1, n_shots=1))
            # Let a few k's fire.
            time.sleep(0.15)
            before = cam.shoot_calls
            sched.cmd_stop_async()
            time.sleep(0.3)
            self.assertEqual(cam.shoot_calls, before)
            self.assertEqual(engine.state, EngineState.IDLE)
        finally:
            sched.shutdown()

    def test_async_switch_works(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start_async(_cfg(name="slow", interval_s=1.0, n_shots=1))
            # Let k=0 fire.
            time.sleep(0.05)
            self.assertEqual(engine.active_config.name, "slow")
            sched.cmd_switch_async(_cfg(name="fast", interval_s=0.05, n_shots=1))
            time.sleep(0.18)
            self.assertEqual(engine.active_config.name, "fast")
            self.assertGreaterEqual(cam.shoot_calls, 3)
        finally:
            sched.shutdown()


class TestBlockingCmdsStillWork(unittest.TestCase):
    """Regression: the original blocking API must keep working unchanged."""

    def test_blocking_cmd_start_still_fires(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start(_cfg(interval_s=10.0))
            for _ in range(50):
                if cam.shoot_calls >= 1:
                    break
                time.sleep(0.01)
            self.assertEqual(cam.shoot_calls, 1)
            self.assertEqual(engine.state, EngineState.RUNNING)
        finally:
            sched.shutdown()

    def test_blocking_cmd_stop_still_halts(self):
        cam = FastCamera()
        engine = Engine(cam)
        sched = EngineScheduler(engine)
        sched.start()
        try:
            sched.cmd_start(_cfg(interval_s=0.1))
            time.sleep(0.15)
            before = cam.shoot_calls
            sched.cmd_stop()
            time.sleep(0.3)
            self.assertEqual(cam.shoot_calls, before)
            self.assertEqual(engine.state, EngineState.IDLE)
        finally:
            sched.shutdown()


if __name__ == "__main__":
    unittest.main()
