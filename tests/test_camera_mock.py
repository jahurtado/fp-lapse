"""Tests for `MockCamera`. Stdlib unittest — no extra dependency."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera import (  # noqa: E402
    CameraNotConnected,
    CaptureFailed,
    ExposureMode,
    FocusMode,
    MockCamera,
)


class TestMockCameraLifecycle(unittest.TestCase):
    def test_starts_disconnected(self):
        cam = MockCamera()
        self.assertFalse(cam.is_connected())

    def test_status_without_connect_raises(self):
        cam = MockCamera()
        with self.assertRaises(CameraNotConnected):
            cam.status()

    def test_connect_disconnect_roundtrip(self):
        cam = MockCamera()
        cam.connect()
        self.assertTrue(cam.is_connected())
        cam.disconnect()
        self.assertFalse(cam.is_connected())


class TestMockCameraConfig(unittest.TestCase):
    def setUp(self):
        self.cam = MockCamera(sleep_overhead_s=0.0)
        self.cam.connect()

    def test_info_defaults(self):
        info = self.cam.info()
        self.assertEqual(info.model, "MOCK fp")

    def test_set_params_persist_in_status(self):
        self.cam.set_params(
            shutter_s=1.0,
            aperture=3.5,
            iso=200,
            exposure_mode=ExposureMode.MANUAL,
            focus_mode=FocusMode.MF,
        )
        s = self.cam.status()
        self.assertEqual(s.shutter_s, 1.0)
        self.assertEqual(s.aperture, 3.5)
        self.assertEqual(s.iso, 200)
        self.assertFalse(s.iso_auto)
        self.assertEqual(s.exposure_mode, ExposureMode.MANUAL)
        self.assertEqual(s.focus_mode, FocusMode.MF)

    def test_setting_iso_disables_auto(self):
        # Status currently reports iso_auto=False (mock default).
        # Force-set it on, then ensure set_params(iso=...) flips it off.
        self.cam._iso_auto = True  # type: ignore[attr-defined]
        self.cam.set_params(iso=400)
        s = self.cam.status()
        self.assertFalse(s.iso_auto)
        self.assertEqual(s.iso, 400)

    def test_partial_set_params_leaves_others(self):
        self.cam.set_params(shutter_s=0.5, aperture=8.0, iso=100)
        self.cam.set_params(shutter_s=2.0)  # only shutter
        s = self.cam.status()
        self.assertEqual(s.shutter_s, 2.0)
        self.assertEqual(s.aperture, 8.0)
        self.assertEqual(s.iso, 100)


class TestMockCameraShoot(unittest.TestCase):
    def setUp(self):
        self.cam = MockCamera(sleep_overhead_s=0.0)
        self.cam.connect()

    def test_shoot_returns_current_params(self):
        self.cam.set_params(shutter_s=0.01, aperture=4.0, iso=200)
        r = self.cam.shoot()
        self.assertEqual(r.shutter_s, 0.01)
        self.assertEqual(r.aperture, 4.0)
        self.assertEqual(r.iso, 200)

    def test_shoot_increments_counter(self):
        self.cam.set_params(shutter_s=0.01)
        self.cam.shoot()
        self.cam.shoot()
        self.cam.shoot()
        self.assertEqual(self.cam.shots_taken, 3)

    def test_injected_failure_raises_and_does_not_count(self):
        self.cam.injected_failures.append("AFFailed")
        with self.assertRaises(CaptureFailed) as ctx:
            self.cam.shoot()
        self.assertEqual(ctx.exception.reason, "AFFailed")
        self.assertEqual(self.cam.shots_taken, 0)

    def test_injected_failures_are_consumed_in_order(self):
        self.cam.injected_failures.extend(["AFFailed", "BufferFull"])
        with self.assertRaises(CaptureFailed) as a:
            self.cam.shoot()
        self.assertEqual(a.exception.reason, "AFFailed")
        with self.assertRaises(CaptureFailed) as b:
            self.cam.shoot()
        self.assertEqual(b.exception.reason, "BufferFull")
        # Now the queue is empty; next shoot succeeds.
        r = self.cam.shoot()
        self.assertEqual(self.cam.shots_taken, 1)
        self.assertIsNotNone(r)

    def test_shoot_without_connect_raises(self):
        cam = MockCamera(sleep_overhead_s=0.0)
        with self.assertRaises(CameraNotConnected):
            cam.shoot()


if __name__ == "__main__":
    unittest.main()
