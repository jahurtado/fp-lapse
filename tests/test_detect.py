"""Tests for camera selection (`fp_lapse.camera.detect`).

The decision function `select_camera_kind` is **pure**: it takes a list of
`(vid, pid)` tuples (already read from USB descriptors elsewhere), an
override string, and an `is_darwin` flag, and returns the camera kind to
build. It imports neither `usb` (pyusb) nor any adapter, so it runs on the
Mac with no hardware and no USB enumeration.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera.detect import (  # noqa: E402
    KIND_MOCK,
    KIND_NIKON,
    KIND_SIGMA,
    NIKON_VID,
    select_camera_kind,
)


class TestOverridePrecedence(unittest.TestCase):
    def test_override_mock(self):
        self.assertEqual(
            select_camera_kind([(NIKON_VID, 0x0429)], override="mock", is_darwin=False),
            KIND_MOCK,
        )

    def test_override_sigma_forces_regardless_of_devices(self):
        self.assertEqual(
            select_camera_kind([(NIKON_VID, 0x0429)], override="sigma_fp", is_darwin=False),
            KIND_SIGMA,
        )

    def test_override_nikon_forces_regardless_of_devices(self):
        self.assertEqual(
            select_camera_kind([], override="nikon_d5600", is_darwin=True),
            KIND_NIKON,
        )

    def test_override_beats_darwin(self):
        self.assertEqual(
            select_camera_kind([], override="sigma_fp", is_darwin=True),
            KIND_SIGMA,
        )

    def test_unknown_override_is_ignored(self):
        # A typo'd override falls through to auto-detect / darwin default,
        # rather than building a non-existent adapter.
        self.assertEqual(
            select_camera_kind([], override="banana", is_darwin=True),
            KIND_MOCK,
        )


class TestDarwinDefault(unittest.TestCase):
    def test_darwin_no_override_is_mock(self):
        self.assertEqual(
            select_camera_kind([(NIKON_VID, 0x0429)], override=None, is_darwin=True),
            KIND_MOCK,
        )


class TestAutoDetect(unittest.TestCase):
    def test_nikon_vid_selects_nikon(self):
        self.assertEqual(
            select_camera_kind([(NIKON_VID, 0x0429)], override=None, is_darwin=False),
            KIND_NIKON,
        )

    def test_nikon_among_other_devices(self):
        devices = [(0x1d6b, 0x0002), (0x0bda, 0x8153), (NIKON_VID, 0x0429)]
        self.assertEqual(
            select_camera_kind(devices, override=None, is_darwin=False),
            KIND_NIKON,
        )

    def test_no_match_non_darwin_falls_back_to_sigma(self):
        # No Nikon and no recognised Sigma → preserve today's single-camera
        # default (Sigma) rather than failing.
        self.assertEqual(
            select_camera_kind([(0x1d6b, 0x0002)], override=None, is_darwin=False),
            KIND_SIGMA,
        )

    def test_empty_device_list_non_darwin_falls_back_to_sigma(self):
        self.assertEqual(
            select_camera_kind([], override=None, is_darwin=False),
            KIND_SIGMA,
        )

    def test_accepts_one_shot_iterator(self):
        # `devices` may be any iterable, including a one-shot iterator. The
        # function scans it twice (VIDs, then full pairs), so it must
        # materialise the input internally; otherwise the second pass would see
        # an exhausted iterator. Defensive guard for that `list(devices)`.
        gen = iter([(0x1d6b, 0x0002), (NIKON_VID, 0x0429)])
        self.assertEqual(
            select_camera_kind(gen, override=None, is_darwin=False),
            KIND_NIKON,
        )


class TestBothCamerasPriority(unittest.TestCase):
    def test_both_attached_nikon_wins(self):
        # Documented default: Nikon first when both are present.
        from fp_lapse.camera.detect import SIGMA_FP_VID, SIGMA_FP_PID
        devices = [(SIGMA_FP_VID, SIGMA_FP_PID), (NIKON_VID, 0x0429)]
        self.assertEqual(
            select_camera_kind(devices, override=None, is_darwin=False),
            KIND_NIKON,
        )

    def test_both_attached_override_sigma_wins(self):
        from fp_lapse.camera.detect import SIGMA_FP_VID, SIGMA_FP_PID
        devices = [(SIGMA_FP_VID, SIGMA_FP_PID), (NIKON_VID, 0x0429)]
        self.assertEqual(
            select_camera_kind(devices, override="sigma_fp", is_darwin=False),
            KIND_SIGMA,
        )


class TestEnvOverrideResolution(unittest.TestCase):
    """`override_from_env` folds FP_LAPSE_CAMERA + the legacy FP_LAPSE_MOCK."""

    def test_camera_env_takes_precedence(self):
        from fp_lapse.camera.detect import override_from_env
        self.assertEqual(
            override_from_env({"FP_LAPSE_CAMERA": "nikon_d5600", "FP_LAPSE_MOCK": "1"}),
            "nikon_d5600",
        )

    def test_mock_env_alias(self):
        from fp_lapse.camera.detect import override_from_env
        self.assertEqual(override_from_env({"FP_LAPSE_MOCK": "1"}), "mock")

    def test_no_env_is_none(self):
        from fp_lapse.camera.detect import override_from_env
        self.assertIsNone(override_from_env({}))

    def test_camera_env_lowercased(self):
        from fp_lapse.camera.detect import override_from_env
        self.assertEqual(override_from_env({"FP_LAPSE_CAMERA": "MOCK"}), "mock")


if __name__ == "__main__":
    unittest.main()
