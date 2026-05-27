"""Regression tests for `SigmaFpCamera` capture-mode dispatch.

`sigma_ptpy` is only installed on the Pi via the `[pi]` extra, so this
module cannot be imported on the Mac dev box. These tests do static
source-level checks instead — they pin the contract of the dispatch
logic without requiring the heavy dependency tree. The actual hardware
behaviour is exercised end-to-end on the Pi.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))


class TestShootDispatchesByFocusMode(unittest.TestCase):
    def setUp(self) -> None:
        src = Path(__file__).resolve().parents[1] / "src" / "fp_lapse" / "camera" / "sigma_fp.py"
        self.source = src.read_text()

    def test_imports_CaptureMode_enum(self):
        # NonAFCapt comes from sigma_ptpy.enum.CaptureMode. The import has
        # to exist or the dispatch can't compile at runtime on the Pi.
        self.assertIn("CaptureMode as _CaptureMode", self.source)

    def test_shoot_picks_NonAFCapt_in_MF(self):
        # The dispatch must read the current focus_mode and pick
        # NonAFCapt for MF, GeneralCapt otherwise. With GeneralCapt and
        # a manual-focus lens the fp returns CaptStatus=AFFailed without
        # firing — observed on hardware 2026-05-21.
        self.assertIn("FocusMode.MF", self.source)
        self.assertIn("_CaptureMode.NonAFCapt", self.source)
        self.assertIn("_CaptureMode.GeneralCapt", self.source)

    def test_snap_command_passes_capture_mode(self):
        # `SnapCommand()` without args defaults to GeneralCapt — the
        # original bug. Make sure the call site passes an explicit
        # CaptureMode= keyword so the dispatch above takes effect.
        self.assertIn("SnapCommand(CaptureMode=", self.source)
        self.assertNotIn("snap_command(SnapCommand())", self.source)

    def test_has_probe_liveness_method(self):
        # probe() is the explicit liveness call the health watchdog uses.
        # It must do a real PTP round-trip (get_device_info) and route any
        # transport error through _mark_disconnected so the camera-health
        # thread reconnects. (Source-level check; the dep isn't on the Mac.)
        self.assertIn("def probe(self)", self.source)
        probe = self.source.split("def probe")[1].split("\n    def ")[0]
        self.assertIn("get_device_info", probe)
        self.assertIn("_mark_disconnected", probe)

    def test_connect_forces_manual_exposure(self):
        # ProgramAuto silently overrides our shutter/iso requests. The
        # adapter must force Manual at connect so `set_params` is
        # respected — observed bug 2026-05-21: a "1/30s" config was
        # exposing for 0.4s because the body was on dial P.
        self.assertIn("_ExposureMode.Manual", self.source)
        # Belt-and-suspenders: the Manual force must live inside the
        # connect implementation (currently `_connect_locked`), not
        # just be a stale reference somewhere.
        connect_section = (
            self.source.split("def _connect_locked")[1].split("\n    def ")[0]
        )
        self.assertIn("_ExposureMode.Manual", connect_section)


if __name__ == "__main__":
    unittest.main()
