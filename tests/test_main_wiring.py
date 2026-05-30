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


class TestScheduleLoopWiring(unittest.TestCase):
    """prd2.md §7 — the UI loop must call `maybe_poll()` and `tick()`
    before `app.render()`, in that order, on every iteration."""

    def test_main_loop_invokes_prober_and_evaluator_before_render(self):
        src = inspect.getsource(main_mod.main)
        self.assertIn("time_sync_prober.maybe_poll()", src)
        self.assertIn("schedule_evaluator.tick()", src)
        idx_poll = src.index("time_sync_prober.maybe_poll()")
        idx_tick = src.index("schedule_evaluator.tick()")
        # Find the render call INSIDE the loop body — the second
        # occurrence of `app.render()`. The first one is the initial
        # paint before the loop starts and does NOT carry the
        # poll/tick guarantee.
        first_render = src.index("app.render()")
        second_render = src.index("app.render()", first_render + 1)
        self.assertLess(idx_poll, second_render)
        self.assertLess(idx_tick, second_render)
        # And maybe_poll runs before tick (so on_sync can land first).
        self.assertLess(idx_poll, idx_tick)

    def test_schedule_trio_constructed_and_bound(self):
        src = inspect.getsource(main_mod.main)
        for needle in (
            "ScheduleStateStore(",
            "TrustedClock(",
            "TimeSyncProber(",
            "ScheduleEvaluator(",
            "app.bind_schedule(",
        ):
            self.assertIn(needle, src)

    def test_no_extra_start_or_shutdown_on_schedule(self):
        # The schedule layer has no threads; ensure __main__ does NOT
        # try to start() or shutdown() any of them.
        src = inspect.getsource(main_mod.main)
        for needle in (
            "schedule_evaluator.start",
            "schedule_evaluator.shutdown",
            "time_sync_prober.start",
            "time_sync_prober.shutdown",
        ):
            self.assertNotIn(needle, src)


class TestButtonRouterUnchanged(unittest.TestCase):
    """prd2.md §6.1: LEFT must NOT acquire long-press machinery."""

    def test_left_long_press_not_armed(self):
        from fp_lapse.buttons.iface import ButtonId
        src = inspect.getsource(main_mod.ButtonRouter)
        # The long-press arm only fires on OK; if LEFT ever appears in
        # the arm path this regression catches it.
        self.assertIn("if bid == ButtonId.OK:", src)
        # The arm helper must not be invoked for any non-OK button. A
        # broader assertion: only the OK branch should call
        # `_arm_long_press`.
        arm_count = src.count("_arm_long_press")
        # One call from on_press(OK) + the method definition itself.
        self.assertLessEqual(arm_count, 3)
        # And no explicit check on ButtonId.LEFT in the press path.
        # (A future implementer adding `if bid == ButtonId.LEFT: …` to
        # arm a timer would land in `on_press`; this test fails first.)
        self.assertNotIn("ButtonId.LEFT", src)


if __name__ == "__main__":
    unittest.main()
