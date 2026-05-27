"""Regression tests for `fp_lapse.__main__` wiring.

These don't exercise the full loop — they pin the contract between
`__main__` and the adapter modules so that a wiring bug (wrong factory
name, missing constructor argument, etc.) is caught at unit-test time
instead of at boot on the Pi.
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse import __main__ as main_mod  # noqa: E402


class TestBuildButtonsWiring(unittest.TestCase):
    def test_gpio_branch_uses_factory(self):
        # `GpioButtonPanel` is a frozen-ish dataclass whose generated
        # __init__ requires `buttons`. Constructing it bare crashes at
        # startup on the Pi. The hardware-correct entry point is the
        # `create()` classmethod which instantiates the gpiozero
        # `Button`s for the pin map. Pin that contract here.
        src = inspect.getsource(main_mod._build_buttons)
        self.assertIn("GpioButtonPanel.create()", src)
        self.assertNotIn(
            "GpioButtonPanel()", src,
            "must call GpioButtonPanel.create(), not the dataclass __init__",
        )


class TestBuildCameraWiring(unittest.TestCase):
    def test_build_camera_returns_proxy(self):
        # `_build_camera()` must return a `CameraProxy` so the live adapter
        # can be hot-swapped behind a stable reference. On the Mac the proxy
        # resolves to the mock and connects.
        from fp_lapse.camera.proxy import CameraProxy
        cam = main_mod._build_camera()
        self.assertIsInstance(cam, CameraProxy)
        self.assertTrue(cam.is_connected())

    def test_build_camera_swallows_connect_failure(self):
        # The app must still boot if the camera is absent (today's
        # behaviour): a connect() failure is logged, not raised.
        from fp_lapse.camera.proxy import CameraProxy

        # A proxy whose adapter fails to connect must not raise out of
        # connect() unguarded at the call site: the build helper wraps it in
        # try/except so the app boots camera-less. Verify both the proxy
        # propagates (so the wrap is meaningful) and the helper wraps it.
        proxy = CameraProxy(detector=lambda: "sigma_fp",
                            factory=lambda kind: _FailingAdapter())
        with self.assertRaises(RuntimeError):
            proxy.connect()
        src = inspect.getsource(main_mod._build_camera)
        self.assertIn("try:", src)
        self.assertIn("CameraProxy", src)


class _FailingAdapter:
    def connect(self):
        raise RuntimeError("no camera")

    def is_connected(self):
        return False


if __name__ == "__main__":
    unittest.main()
