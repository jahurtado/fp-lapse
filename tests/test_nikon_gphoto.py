"""Regression tests for `NikonGPhotoCamera`.

`gphoto2` (python-gphoto2 → libgphoto2) is only installed on the Pi via the
`[nikon]` extra, so this module cannot be imported on the Mac dev box. Like
`test_sigma_fp_dispatch.py`, these are static source-level checks that pin
the contract of the adapter without requiring the heavy dependency tree. The
actual hardware behaviour is exercised end-to-end on the Pi (validation.md).

The pure value-translation logic the adapter relies on is tested separately
and fully on the Mac in `test_nikon_values.py`.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

_SRC = Path(__file__).resolve().parents[1] / "src" / "fp_lapse" / "camera" / "nikon_gphoto.py"


class TestModuleStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()
        self.tree = ast.parse(self.source)

    def _class(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
        return None

    def test_imports_gphoto2_at_module_top(self):
        # Mirrors sigma_fp.py importing sigma_ptpy at module top.
        self.assertIn("import gphoto2", self.source)

    def test_imports_pure_value_helpers(self):
        # The nearest-match logic must come from nikon_values (pure, no
        # gphoto2), not be re-implemented inline.
        self.assertIn("from .nikon_values import", self.source)

    def test_not_imported_at_package_level(self):
        # __init__.py must NOT import the gphoto2-backed adapter, or
        # `import fp_lapse.camera` breaks on the Mac.
        init = (_SRC.parent / "__init__.py").read_text()
        self.assertNotIn("nikon_gphoto", init)
        self.assertNotIn("NikonGPhotoCamera", init)

    def test_class_defined(self):
        self.assertIsNotNone(self._class("NikonGPhotoCamera"))

    def test_satisfies_camera_protocol_methods(self):
        cls = self._class("NikonGPhotoCamera")
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        for required in (
            "connect", "disconnect", "is_connected", "probe",
            "info", "status", "set_params", "shoot",
        ):
            self.assertIn(required, methods, f"missing Protocol method {required}")

    def test_has_rlock(self):
        self.assertIn("threading.RLock()", self.source)

    def test_has_mark_disconnected_helper(self):
        self.assertIn("def _mark_disconnected", self.source)
        # Drops the handle so camera_health recovers via connect().
        md = self.source.split("def _mark_disconnected")[1].split("\n    def ")[0]
        self.assertIn("self._cam = None", md)

    def test_has_require_helper(self):
        self.assertIn("def _require", self.source)


class TestConnectContract(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def _connect_section(self) -> str:
        # Both connect() and _connect_locked() bodies.
        return self.source

    def test_sets_capturetarget_to_card(self):
        # Shots must land on the SD card (trigger-only), mirroring the
        # Sigma's DestToSave=InCamera.
        self.assertIn("capturetarget", self.source.lower())

    def test_idempotent_connect_guard(self):
        # if self._cam is not None: return — like SigmaFpCamera.connect.
        connect = self.source.split("def connect")[1].split("\n    def ")[0]
        self.assertIn("self._cam is not None", connect)


class TestExposureModeReadOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def test_set_params_never_sets_expprogram(self):
        # The dial (expprogram) is read-only over USB. The adapter must
        # never try to SET it. We allow reading it (get) but not setting.
        # Config-widget writes go through the `_set_choice(config, name, …)`
        # helper. The adapter must never call it with the read-only
        # "expprogram" widget. (It may *read* expprogram to detect a
        # mismatch, which is a get, not a _set_choice.)
        self.assertNotIn('_set_choice(config, "expprogram"', self.source)
        self.assertNotIn("_set_choice(config, 'expprogram'", self.source)
        # Positive contract: set_params records a dial mismatch flag.
        set_params = self.source.split("def set_params")[1].split("\n    def ")[0]
        self.assertIn("_dial_mismatch", set_params)

    def test_set_params_logs_warning_on_mismatch(self):
        set_params = self.source.split("def set_params")[1].split("\n    def ")[0]
        self.assertIn("warning", set_params.lower())

    def test_dial_mismatch_surfaced_in_status(self):
        # status() must reflect the dial so the UI can show "DIAL NOT ON M".
        self.assertIn("_dial_mismatch", self.source)


class TestShootContract(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def test_trigger_only_no_download(self):
        # gp_camera_trigger_capture / trigger_capture, NOT
        # capture-image-and-download.
        self.assertIn("trigger_capture", self.source)
        self.assertNotIn("capture-image-and-download", self.source)

    def test_returns_capture_result_from_cached_params(self):
        shoot = self.source.split("def shoot")[1]
        self.assertIn("CaptureResult", shoot)
        self.assertIn("_last_shutter_s", self.source)

    def test_mf_uses_non_af_path(self):
        # MF must take a non-AF capture path so AF doesn't hunt between
        # frames — analogous to the Sigma's NonAFCapt branch.
        self.assertIn("FocusMode.MF", self.source)


class TestErrorMapping(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def test_maps_gphoto2_error(self):
        self.assertIn("GPhoto2Error", self.source)

    def test_raises_camera_exceptions(self):
        self.assertIn("CameraNotConnected", self.source)
        self.assertIn("CameraBusy", self.source)
        self.assertIn("CaptureFailed", self.source)

    def test_logs_root_hint_on_io_problem(self):
        # The spike: as user `pi`, libusb_open fails and surfaces as a
        # misleading 'I/O problem' (-7). Log a clear root-required hint.
        self.assertIn("root", self.source.lower())

    def test_classify_resolves_error_codes_safely(self):
        # Error-code constants must be resolved via getattr/hasattr (collected
        # into `_IO_ERROR_CODES` at import) so a constant absent from the
        # installed libgphoto2 binding is skipped — never an AttributeError
        # raised INSIDE the `except gp.GPhoto2Error` handler (which would mask
        # the real error and bypass _mark_disconnected). Regression for the
        # review warning about mixed direct / getattr constant access.
        self.assertIn("_IO_ERROR_CODES", self.source)
        classify = self.source.split("def _classify")[1].split("\n    def ")[0]
        # No direct `gp.GP_ERROR_*` attribute access inside _classify.
        self.assertNotIn("gp.GP_ERROR", classify)


class TestStatusContract(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def test_populates_battery_pct(self):
        status = self.source.split("def status")[1].split("\n    def ")[0]
        self.assertIn("battery_pct", status)
        self.assertIn("batterylevel", self.source.lower())


class TestProbeLiveness(unittest.TestCase):
    """`probe()` is `camera_health`'s explicit disconnect probe, so it must
    FAIL when the body is unplugged. Hardware finding (2026-05-27): on the
    D5600, after a USB unplug libgphoto2 keeps returning a CACHED config —
    `get_config()` stayed OK indefinitely while `get_summary()`/
    `get_storageinfo()` flipped to GP_ERROR_IO. So probe() must do a real
    round-trip (`get_summary()`) that fails on unplug; the earlier design
    (info() doubling as the probe via get_config) meant a Nikon unplug went
    unnoticed by the 5 s health watchdog until the next shoot() error (and the
    hot-swap never fired). info() is now identity-only — no liveness call.
    Regression for that bug.
    """

    def setUp(self) -> None:
        self.source = _SRC.read_text()

    def _body(self, name: str) -> str:
        return self.source.split(f"def {name}")[1].split("\n    def ")[0]

    def test_probe_method_exists(self):
        self.assertIn("def probe(self)", self.source)

    def test_probe_does_real_roundtrip(self):
        # get_summary() errors on unplug (get_config does NOT). Match the
        # actual call (`cam.`-prefixed), not comment mentions.
        probe = self._body("probe")
        self.assertIn("cam.get_summary()", probe)

    def test_probe_marks_disconnected_on_transport_error(self):
        probe = self._body("probe")
        self.assertIn("_mark_disconnected", probe)

    def test_info_is_identity_only_no_liveness_call(self):
        # info() must NOT carry the get_summary liveness call anymore — that
        # responsibility moved to probe(). info() only reads identity widgets
        # off get_config().
        info = self._body("info")
        self.assertNotIn("get_summary", info)
        self.assertIn("cam.get_config()", info)


if __name__ == "__main__":
    unittest.main()
