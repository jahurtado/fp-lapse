"""Tests for the runtime hot-swap proxy (`fp_lapse.camera.proxy`).

`CameraProxy` satisfies the `Camera` Protocol, holds the live inner adapter,
and swaps it inside `connect()` when re-detection reports a different kind.
The detector and the adapter factory are **injectable** so these tests run
on the Mac with no `usb` / `gphoto2`: we feed a fake detector returning kinds
and fake adapter objects.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera.iface import (  # noqa: E402
    CameraInfo,
    CameraNotConnected,
    CameraStatus,
    CaptureResult,
    ExposureMode,
    FocusMode,
)
from fp_lapse.camera.proxy import CameraProxy  # noqa: E402


class FakeAdapter:
    """A `Camera`-Protocol fake recording calls, with its own lock."""

    def __init__(self, *, model: str, kind: str) -> None:
        self.model = model
        self.kind = kind
        self._lock = threading.RLock()
        self._connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.probe_calls = 0
        self.shots = 0
        self.fail_connect = False

    def connect(self) -> None:
        with self._lock:
            self.connect_calls += 1
            if self.fail_connect:
                raise CameraNotConnected("fake connect failure")
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self.disconnect_calls += 1
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def probe(self) -> None:
        with self._lock:
            self.probe_calls += 1
            if not self._connected:
                raise CameraNotConnected("fake not connected")

    def info(self) -> CameraInfo:
        with self._lock:
            if not self._connected:
                raise CameraNotConnected("fake not connected")
            return CameraInfo(model=self.model, firmware="1.0", serial="X")

    def status(self) -> CameraStatus:
        with self._lock:
            if not self._connected:
                raise CameraNotConnected("fake not connected")
            return CameraStatus(
                shutter_s=0.002, aperture=5.6, iso=800,
                iso_auto=False, exposure_mode=ExposureMode.MANUAL,
                focus_mode=FocusMode.MF, battery_pct=77,
            )

    def set_params(self, **kwargs) -> None:
        with self._lock:
            if not self._connected:
                raise CameraNotConnected("fake not connected")

    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        with self._lock:
            if not self._connected:
                raise CameraNotConnected("fake not connected")
            self.shots += 1
            return CaptureResult(shutter_s=0.002, aperture=5.6, iso=800, duration_s=0.1)


def _make_proxy(detect_sequence, *, registry=None):
    """Build a proxy with a fake detector and adapter factory.

    `detect_sequence` is a list of kinds returned by successive
    `detector()` calls (the last one repeats). `registry` maps kind →
    FakeAdapter; created lazily if not supplied.
    """
    reg = registry if registry is not None else {}
    seq = list(detect_sequence)

    def detector():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def factory(kind):
        if kind not in reg:
            reg[kind] = FakeAdapter(model=kind.upper(), kind=kind)
        return reg[kind]

    proxy = CameraProxy(detector=detector, factory=factory)
    return proxy, reg


class TestProxyDelegation(unittest.TestCase):
    def test_connect_builds_and_connects_inner(self):
        proxy, reg = _make_proxy(["sigma_fp"])
        proxy.connect()
        self.assertTrue(proxy.is_connected())
        self.assertEqual(reg["sigma_fp"].connect_calls, 1)

    def test_info_delegates(self):
        proxy, reg = _make_proxy(["nikon_d5600"])
        proxy.connect()
        self.assertEqual(proxy.info().model, "NIKON_D5600")

    def test_probe_delegates(self):
        proxy, reg = _make_proxy(["sigma_fp"])
        proxy.connect()
        proxy.probe()
        self.assertEqual(reg["sigma_fp"].probe_calls, 1)

    def test_status_delegates(self):
        proxy, _ = _make_proxy(["nikon_d5600"])
        proxy.connect()
        self.assertEqual(proxy.status().battery_pct, 77)

    def test_shoot_delegates(self):
        proxy, reg = _make_proxy(["sigma_fp"])
        proxy.connect()
        r = proxy.shoot()
        self.assertIsInstance(r, CaptureResult)
        self.assertEqual(reg["sigma_fp"].shots, 1)

    def test_disconnect_delegates(self):
        proxy, reg = _make_proxy(["sigma_fp"])
        proxy.connect()
        proxy.disconnect()
        self.assertFalse(proxy.is_connected())
        self.assertEqual(reg["sigma_fp"].disconnect_calls, 1)


class TestProxyNoInner(unittest.TestCase):
    def test_is_connected_false_before_connect(self):
        proxy, _ = _make_proxy(["sigma_fp"])
        self.assertFalse(proxy.is_connected())

    def test_protocol_calls_raise_when_no_inner(self):
        proxy, _ = _make_proxy(["sigma_fp"])
        with self.assertRaises(CameraNotConnected):
            proxy.probe()
        with self.assertRaises(CameraNotConnected):
            proxy.info()
        with self.assertRaises(CameraNotConnected):
            proxy.status()
        with self.assertRaises(CameraNotConnected):
            proxy.set_params(iso=800)
        with self.assertRaises(CameraNotConnected):
            proxy.shoot()

    def test_connect_failure_leaves_inner_but_disconnected(self):
        # Detector resolves a kind, factory builds the adapter, but the
        # adapter's connect() fails (camera absent). The proxy must
        # propagate so camera_health logs + retries; is_connected stays
        # False so the health loop re-enters connect() next tick.
        reg = {"sigma_fp": FakeAdapter(model="SIG", kind="sigma_fp")}
        reg["sigma_fp"].fail_connect = True
        proxy, _ = _make_proxy(["sigma_fp"], registry=reg)
        with self.assertRaises(CameraNotConnected):
            proxy.connect()
        self.assertFalse(proxy.is_connected())


class TestProxyHotSwap(unittest.TestCase):
    def test_swap_when_kind_changes(self):
        # First connect → sigma; second connect re-detects nikon and swaps.
        proxy, reg = _make_proxy(["sigma_fp", "nikon_d5600"])
        proxy.connect()
        self.assertEqual(proxy.info().model, "SIGMA_FP")
        sigma = reg["sigma_fp"]

        proxy.connect()  # health-thread tick re-detects a different body
        self.assertEqual(proxy.info().model, "NIKON_D5600")
        # Old adapter disconnected before the new one was built/connected.
        self.assertEqual(sigma.disconnect_calls, 1)
        self.assertEqual(reg["nikon_d5600"].connect_calls, 1)

    def test_no_swap_when_kind_unchanged(self):
        # Same camera came back (brown-out / cable wobble): keep the inner
        # adapter, just reconnect it; do NOT rebuild.
        proxy, reg = _make_proxy(["sigma_fp", "sigma_fp"])
        proxy.connect()
        sigma = reg["sigma_fp"]
        sigma.disconnect()  # simulate the drop the health thread observed
        proxy.connect()
        # Same instance, no extra adapter created.
        self.assertIs(proxy._inner, sigma)  # type: ignore[attr-defined]
        self.assertEqual(len(reg), 1)

    def test_current_kind_tracks_swap(self):
        proxy, _ = _make_proxy(["sigma_fp", "nikon_d5600"])
        proxy.connect()
        self.assertEqual(proxy.current_kind(), "sigma_fp")
        proxy.connect()
        self.assertEqual(proxy.current_kind(), "nikon_d5600")

    def test_mock_kind_builds_mock_adapter(self):
        # Proxy can also host the mock (Mac dev / override path).
        proxy, reg = _make_proxy(["mock"])
        proxy.connect()
        self.assertTrue(proxy.is_connected())
        self.assertEqual(proxy.current_kind(), "mock")


class TestProxyUiHelpers(unittest.TestCase):
    def test_model_label_default_when_no_inner(self):
        # Before any connect, the UI shows the legacy "fp" label.
        proxy, _ = _make_proxy(["sigma_fp"])
        self.assertEqual(proxy.model_label(), "fp")

    def test_model_label_short_sigma(self):
        # A model string containing "fp" collapses to "fp".
        reg = {"sigma_fp": FakeAdapter(model="SIGMA fp", kind="sigma_fp")}
        proxy, _ = _make_proxy(["sigma_fp"], registry=reg)
        proxy.connect()
        self.assertEqual(proxy.model_label(), "fp")

    def test_model_label_short_nikon(self):
        reg = {"nikon_d5600": FakeAdapter(model="Nikon D5600", kind="nikon_d5600")}
        proxy, _ = _make_proxy(["nikon_d5600"], registry=reg)
        proxy.connect()
        self.assertEqual(proxy.model_label(), "D5600")

    def test_model_label_updates_on_swap(self):
        reg = {
            "sigma_fp": FakeAdapter(model="SIGMA fp", kind="sigma_fp"),
            "nikon_d5600": FakeAdapter(model="Nikon D5600", kind="nikon_d5600"),
        }
        proxy, _ = _make_proxy(["sigma_fp", "nikon_d5600"], registry=reg)
        proxy.connect()
        self.assertEqual(proxy.model_label(), "fp")
        proxy.connect()
        self.assertEqual(proxy.model_label(), "D5600")

    def test_dial_mismatch_false_without_inner(self):
        proxy, _ = _make_proxy(["sigma_fp"])
        self.assertFalse(proxy.dial_mismatch())

    def test_dial_mismatch_false_for_adapter_without_attr(self):
        # The Sigma adapter has no dial concept → never a mismatch.
        proxy, _ = _make_proxy(["sigma_fp"])
        proxy.connect()
        self.assertFalse(proxy.dial_mismatch())

    def test_dial_mismatch_reads_inner_attribute(self):
        adapter = FakeAdapter(model="Nikon D5600", kind="nikon_d5600")
        adapter.dial_mismatch = True  # mimic NikonGPhotoCamera.dial_mismatch
        reg = {"nikon_d5600": adapter}
        proxy, _ = _make_proxy(["nikon_d5600"], registry=reg)
        proxy.connect()
        self.assertTrue(proxy.dial_mismatch())


class TestProxyThreadSafety(unittest.TestCase):
    def test_concurrent_connect_and_shoot(self):
        # A connect() (swap) racing a shoot() must not corrupt state or
        # raise an unexpected error. Each adapter's own RLock protects its
        # operations; the proxy swaps the reference under a short lock.
        proxy, _ = _make_proxy(["sigma_fp", "sigma_fp", "sigma_fp"])
        proxy.connect()
        errors = []

        def hammer_shoot():
            for _ in range(50):
                try:
                    proxy.shoot()
                except CameraNotConnected:
                    pass  # acceptable during a swap window
                except Exception as e:  # pragma: no cover
                    errors.append(e)

        def hammer_connect():
            for _ in range(50):
                try:
                    proxy.connect()
                except Exception as e:  # pragma: no cover
                    errors.append(e)

        threads = [threading.Thread(target=hammer_shoot),
                   threading.Thread(target=hammer_connect)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
