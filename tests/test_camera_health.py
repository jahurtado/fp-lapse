"""Tests for `CameraHealth` — the camera auto-reconnect thread."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera import CameraInfo, CameraNotConnected  # noqa: E402
from fp_lapse.camera_health import CameraHealth  # noqa: E402


class FlakyCamera:
    """Stub camera with controllable is_connected() / connect() / info().

    `info()` is the probe used by the health thread to detect silent
    disconnects. It raises `CameraNotConnected` (and flips the
    internal `_connected` flag to False) when `probe_will_fail` is
    set — that's how we simulate "cable was yanked while the engine
    was idle".
    """

    def __init__(self, *, connected_initially: bool = False,
                 connect_succeeds_after: int = 0,
                 raise_on_connect: bool = False) -> None:
        self._connected = connected_initially
        self._connect_succeeds_after = connect_succeeds_after
        self._raise_on_connect = raise_on_connect
        self.connect_calls = 0
        self.info_calls = 0
        # When True, the next `info()` call simulates a transport
        # error: flips `_connected` to False and raises.
        self.probe_will_fail = False

    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self.connect_calls += 1
        if self._raise_on_connect:
            raise RuntimeError("simulated USB failure")
        if self.connect_calls > self._connect_succeeds_after:
            self._connected = True

    def info(self) -> CameraInfo:
        self.info_calls += 1
        if self.probe_will_fail:
            self._connected = False
            self.probe_will_fail = False  # one-shot
            raise CameraNotConnected("simulated USB drop during probe")
        if not self._connected:
            raise CameraNotConnected("not connected")
        return CameraInfo(model="FLAKY", firmware="0", serial="0")

    def force_disconnect(self) -> None:
        """Simulate a mid-session USB drop (silent — no probe needed)."""
        self._connected = False


def _wait_for(predicate, timeout: float = 1.0, poll: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(poll)
    return False


class TestCameraHealth(unittest.TestCase):
    def test_probes_when_connected_does_not_reconnect(self):
        # When connected, the thread probes via `info()` to detect
        # silent disconnects but does NOT call connect() again.
        cam = FlakyCamera(connected_initially=True)
        health = CameraHealth(cam, interval_s=0.05)
        health.start()
        try:
            time.sleep(0.18)
            self.assertEqual(cam.connect_calls, 0)
            self.assertGreaterEqual(cam.info_calls, 2)
        finally:
            health.shutdown()

    def test_probe_failure_triggers_reconnect_on_next_tick(self):
        # Scenario: camera was connected, then the cable was yanked
        # while the engine was idle. The probe is what discovers it.
        # After the probe fails, the adapter is in the disconnected
        # state and the NEXT tick takes the reconnect branch.
        cam = FlakyCamera(connected_initially=True)
        health = CameraHealth(cam, interval_s=0.04)
        health.start()
        try:
            time.sleep(0.06)  # one probe succeeds
            self.assertEqual(cam.connect_calls, 0)
            cam.probe_will_fail = True
            # Wait long enough for the failing probe + a subsequent
            # reconnect attempt.
            self.assertTrue(_wait_for(lambda: cam.connect_calls >= 1,
                                       timeout=0.5))
            self.assertTrue(_wait_for(lambda: cam.is_connected(),
                                       timeout=0.5))
        finally:
            health.shutdown()

    def test_reconnects_when_disconnected(self):
        cam = FlakyCamera(connected_initially=False)
        health = CameraHealth(cam, interval_s=0.05)
        health.start()
        try:
            self.assertTrue(_wait_for(lambda: cam.is_connected(), timeout=1.0))
            self.assertGreaterEqual(cam.connect_calls, 1)
        finally:
            health.shutdown()

    def test_keeps_retrying_until_success(self):
        # Camera will reject the first 2 attempts and accept the 3rd.
        cam = FlakyCamera(connected_initially=False, connect_succeeds_after=2)
        health = CameraHealth(cam, interval_s=0.04)
        health.start()
        try:
            self.assertTrue(_wait_for(lambda: cam.is_connected(), timeout=1.0))
            self.assertGreaterEqual(cam.connect_calls, 3)
        finally:
            health.shutdown()

    def test_handles_connect_exception_and_retries(self):
        # `connect()` raises every time. The thread must not die — it
        # should keep ticking and log each failure.
        cam = FlakyCamera(connected_initially=False, raise_on_connect=True)
        health = CameraHealth(cam, interval_s=0.03)
        health.start()
        try:
            time.sleep(0.2)
            # At least 3 attempts in 200 ms at 30 ms cadence.
            self.assertGreaterEqual(cam.connect_calls, 3)
            self.assertFalse(cam.is_connected())
        finally:
            health.shutdown()

    def test_sets_dirty_event_on_reconnect(self):
        cam = FlakyCamera(connected_initially=False)
        dirty = threading.Event()
        health = CameraHealth(cam, interval_s=0.03, dirty_event=dirty)
        health.start()
        try:
            self.assertTrue(dirty.wait(timeout=1.0))
        finally:
            health.shutdown()

    def test_does_not_set_dirty_on_unchanged_state(self):
        cam = FlakyCamera(connected_initially=True)
        dirty = threading.Event()
        health = CameraHealth(cam, interval_s=0.03, dirty_event=dirty)
        health.start()
        try:
            time.sleep(0.15)
            self.assertFalse(dirty.is_set())
        finally:
            health.shutdown()

    def test_mid_session_disconnect_triggers_reconnect(self):
        cam = FlakyCamera(connected_initially=True)
        health = CameraHealth(cam, interval_s=0.04)
        health.start()
        try:
            time.sleep(0.1)  # idle phase
            self.assertEqual(cam.connect_calls, 0)
            cam.force_disconnect()
            self.assertTrue(_wait_for(lambda: cam.is_connected(), timeout=1.0))
            self.assertGreaterEqual(cam.connect_calls, 1)
        finally:
            health.shutdown()

    def test_shutdown_joins_thread(self):
        cam = FlakyCamera(connected_initially=True)
        health = CameraHealth(cam, interval_s=0.05)
        health.start()
        health.shutdown(timeout=1.0)
        self.assertFalse(health._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
