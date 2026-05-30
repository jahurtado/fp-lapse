"""Tests for the `App` orchestrator: screen state transitions and
dispatch to engine + store.

Uses a real `MockCamera` (fast with `sleep_overhead_s=0`) and a
`ConfigStore` backed by a temp file. The engine is injected with a
fake monotonic clock so long-press detection is deterministic.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from datetime import datetime, date as date_t, time as time_t  # noqa: E402
import threading  # noqa: E402

from fp_lapse.app import App, AppState  # noqa: E402
from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.camera import MockCamera  # noqa: E402
from fp_lapse.configs import ConfigStore, Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import Engine, EngineState  # noqa: E402
from fp_lapse.schedule import (  # noqa: E402
    ScheduleEvaluator,
    ScheduleStateStore,
    SyncOutcome,
    TimeSyncProber,
    TrustedClock,
)
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    MainAction,
    MainActionResult,
    ScheduleIndicator,
    TimeSetupMenuAction,
)


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _SyncScheduler:
    """Stand-in for `EngineScheduler` that forwards commands synchronously.

    Lets unit tests drive the app without the real scheduler thread —
    `engine.tick()` is not called automatically; tests advance the
    clock and call `engine.tick()` themselves when they need to.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        self.async_calls: list[tuple[str, object]] = []

    def cmd_start(self, cfg) -> None:
        try:
            self.engine.start(cfg)
        except Exception:
            pass

    def cmd_stop(self) -> None:
        self.engine.stop()

    def cmd_switch(self, cfg) -> None:
        try:
            self.engine.switch_to(cfg)
        except Exception:
            pass

    # Async variants (no-op record-keeping for tests).
    def cmd_start_async(self, cfg) -> None:
        self.async_calls.append(("start", cfg))

    def cmd_stop_async(self) -> None:
        self.async_calls.append(("stop", None))

    def cmd_switch_async(self, cfg) -> None:
        self.async_calls.append(("switch", cfg))


def _make_app(test: unittest.TestCase, initial_configs=(), *, now_monotonic=None):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "configs.json"
    store = ConfigStore(path)
    if initial_configs:
        store.save(list(initial_configs))
    camera = MockCamera(sleep_overhead_s=0.0)
    camera.connect()
    clock = _Clock()
    engine = Engine(camera, now_monotonic=clock.now)
    scheduler = _SyncScheduler(engine)
    kw = {}
    if now_monotonic is not None:
        kw["now_monotonic"] = now_monotonic
    app = App(scheduler=scheduler, store=store, camera=camera, **kw)
    return app, clock, camera


A = TimelapseConfig("A", 10.0, (Shot(shutter=1 / 500, iso=200),))
B = TimelapseConfig("B", 5.0, (Shot(shutter=1 / 1000, iso=400),))


class TestAppLifecycle(unittest.TestCase):
    def test_loads_configs_on_init(self):
        app, _, _ = _make_app(self, initial_configs=(A, B))
        self.assertEqual([c.name for c in app.configs], ["A", "B"])
        self.assertEqual(app.state, AppState.MAIN)

    def test_starts_in_main(self):
        app, _, _ = _make_app(self)
        self.assertEqual(app.state, AppState.MAIN)

    def test_render_returns_320x240(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        img = app.render()
        self.assertEqual(img.size, (320, 240))

    def test_render_uses_camera_model_label_and_dial(self):
        # The App must surface the proxy's model_label()/dial_mismatch()
        # into the rendered status bar, without touching the engine. A
        # camera fake exposing those methods (like CameraProxy) changes the
        # pixels vs. a plain MockCamera default ("fp", no warning).
        app, _, _ = _make_app(self, initial_configs=(A,))
        plain = app.render().tobytes()

        class _ProxyLike(MockCamera):
            def model_label(self_inner):
                return "D5600"

            def dial_mismatch(self_inner):
                return True

        app.camera = _ProxyLike(sleep_overhead_s=0.0)
        app.camera.connect()
        # Engine still holds the original camera; we only swapped the App's
        # reference, which is what feeds _render_main_screen. That's enough
        # to prove the UI reads the label/warning from app.camera.
        self.assertEqual(app._camera_model_label(), "D5600")
        self.assertTrue(app._camera_dial_mismatch())
        labelled = app.render().tobytes()
        self.assertNotEqual(plain, labelled)

    def test_render_defaults_for_plain_camera(self):
        # MockCamera has no model_label()/dial_mismatch() → safe defaults.
        app, _, _ = _make_app(self, initial_configs=(A,))
        self.assertEqual(app._camera_model_label(), "fp")
        self.assertFalse(app._camera_dial_mismatch())


class TestMainActions(unittest.TestCase):
    def test_ok_starts_engine(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        app.on_press(ButtonId.OK)
        app.on_release(ButtonId.OK)
        self.assertEqual(app.engine.state, EngineState.RUNNING)
        self.assertEqual(app.engine.active_config.name, "A")

    def test_back_in_running_opens_stop_overlay(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.OVERLAY_STOP)

    def test_overlay_ok_stops_engine(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        app.on_press(ButtonId.BACK)             # → OVERLAY_STOP
        app.on_press(ButtonId.OK)               # confirm
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual(app.engine.state, EngineState.IDLE)

    def test_overlay_back_cancels_stop(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        app.on_press(ButtonId.BACK)             # → OVERLAY_STOP
        app.on_press(ButtonId.BACK)             # cancel
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual(app.engine.state, EngineState.RUNNING)

    def test_long_press_opens_manage_menu(self):
        app, clock, _ = _make_app(self, initial_configs=(A,))
        app.on_press(ButtonId.OK)
        app.on_long_press(ButtonId.OK)  # external timer fires
        self.assertEqual(app.state, AppState.MANAGE)


class TestManageMenu(unittest.TestCase):
    def _open_manage(self, app, clock):
        app.on_press(ButtonId.OK)
        app.on_long_press(ButtonId.OK)
        assert app.state == AppState.MANAGE

    def test_cancel_returns_to_main(self):
        app, clock, _ = _make_app(self, initial_configs=(A,))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 3  # Cancel
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.MAIN)

    def test_edit_transitions_to_edit_screen(self):
        app, clock, _ = _make_app(self, initial_configs=(A,))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 0  # Edit
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.EDIT)
        self.assertEqual(app.edit_ix.draft.name, "A")

    def test_duplicate_adds_copy(self):
        app, clock, _ = _make_app(self, initial_configs=(A,))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 1  # Duplicate
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual([c.name for c in app.configs], ["A", "A (copy)"])

    def test_delete_opens_confirmation_overlay(self):
        app, clock, _ = _make_app(self, initial_configs=(A, B))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 2  # Delete
        app.on_press(ButtonId.OK)
        # Should NOT delete yet — it opens the confirmation overlay.
        self.assertEqual(app.state, AppState.OVERLAY_DELETE)
        self.assertEqual([c.name for c in app.configs], ["A", "B"])

    def test_delete_overlay_cancel_returns_to_manage(self):
        app, clock, _ = _make_app(self, initial_configs=(A, B))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 2
        app.on_press(ButtonId.OK)   # → OVERLAY_DELETE
        app.on_press(ButtonId.BACK)  # cancel
        self.assertEqual(app.state, AppState.MANAGE)
        self.assertEqual([c.name for c in app.configs], ["A", "B"])

    def test_delete_overlay_confirm_removes_and_persists(self):
        app, clock, _ = _make_app(self, initial_configs=(A, B))
        self._open_manage(app, clock)
        app.manage_ix.cursor = 2
        app.on_press(ButtonId.OK)   # → OVERLAY_DELETE
        app.on_press(ButtonId.OK)   # confirm
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual([c.name for c in app.configs], ["B"])
        # persisted to disk
        reloaded = ConfigStore(app.store.path).load()
        self.assertEqual([c.name for c in reloaded], ["B"])


class TestEditFlow(unittest.TestCase):
    def test_new_via_save_requires_confirm(self):
        """Creating a new config also goes through OVERLAY_SAVE."""
        app, _, _ = _make_app(self)
        self.assertEqual(app.main_ix.cursor, 0)               # +New
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        self.assertEqual(app.state, AppState.EDIT)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)  # OK → overlay
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        self.assertEqual(app.configs, [])                       # not yet persisted
        app.on_press(ButtonId.OK)                               # confirm (overlay)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual([c.name for c in app.configs], ["Config 1"])

    def test_new_back_in_overlay_returns_to_edit(self):
        """BACK en el overlay de save vuelve al editor; nada se persiste."""
        app, _, _ = _make_app(self)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.EDIT)
        self.assertEqual(app.configs, [])

    def test_back_in_edit_with_no_changes_skips_overlay(self):
        # If nothing changed, BACK goes straight to MAIN without
        # asking — there's nothing to discard.
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        self.assertFalse(app.edit_ix.is_dirty)
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual([c.name for c in app.configs], ["A"])

    def test_back_in_edit_with_changes_opens_discard_overlay(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        # Modify draft so is_dirty becomes True.
        app.edit_ix.on_press(ButtonId.DOWN)   # cursor → interval
        app.edit_ix.on_press(ButtonId.RIGHT)  # cycle value
        self.assertTrue(app.edit_ix.is_dirty)
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.OVERLAY_DISCARD)
        # Config not modified yet
        self.assertEqual(app.configs[0].interval_s, 10.0)

    def test_discard_overlay_cancel_returns_to_edit(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.edit_ix.on_press(ButtonId.DOWN)
        app.edit_ix.on_press(ButtonId.RIGHT)
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.OVERLAY_DISCARD)
        app.on_press(ButtonId.BACK)  # cancel the overlay
        self.assertEqual(app.state, AppState.EDIT)
        # Draft preserved
        self.assertTrue(app.edit_ix.is_dirty)

    def test_discard_overlay_ok_drops_changes(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.edit_ix.on_press(ButtonId.DOWN)
        app.edit_ix.on_press(ButtonId.RIGHT)
        app.on_press(ButtonId.BACK)        # → OVERLAY_DISCARD
        app.on_press(ButtonId.OK)           # confirm discard
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.edit_ix)
        self.assertEqual(app.configs[0].interval_s, 10.0)

    def test_save_existing_dirty_requires_confirm(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        # Modify draft so is_dirty becomes True.
        app.edit_ix.on_press(ButtonId.DOWN)        # cursor → interval
        app.edit_ix.on_press(ButtonId.RIGHT)       # cycle value
        self.assertTrue(app.edit_ix.is_dirty)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)  # request SAVE
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        # Config not persisted yet
        self.assertEqual(app.configs[0].interval_s, 10.0)

    def test_overlay_save_ok_persists(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.edit_ix.on_press(ButtonId.DOWN)
        app.edit_ix.on_press(ButtonId.RIGHT)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)  # SAVE → OVERLAY_SAVE
        app.on_press(ButtonId.OK)                                # confirm (overlay)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertNotEqual(app.configs[0].interval_s, 10.0)

    def test_overlay_save_back_returns_to_edit(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.edit_ix.on_press(ButtonId.DOWN)
        app.edit_ix.on_press(ButtonId.RIGHT)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)  # SAVE → OVERLAY_SAVE
        app.on_press(ButtonId.BACK)                              # cancel
        self.assertEqual(app.state, AppState.EDIT)
        # Draft survives; original list untouched.
        self.assertEqual(app.configs[0].interval_s, 10.0)
        self.assertTrue(app.edit_ix.is_dirty)

    def test_save_existing_clean_also_confirms(self):
        """OK sobre una config sin cambios sigue mostrando el overlay
        (regla "siempre confirma"). Confirmando es un no-op funcional
        (escribe los mismos bytes); cancelando vuelve a edit."""
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)  # OK → overlay
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        app.on_press(ButtonId.OK)                                # confirm (overlay)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual(app.configs[0].interval_s, 10.0)


class _FakeMonotonic:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _SpyProber:
    """Stand-in for `TimeSyncProber` that records `request_force_sync`."""

    def __init__(self) -> None:
        self.force_calls: int = 0
        self.force_poll_calls: int = 0
        self._last_mono = None

    def request_force_sync(self) -> None:
        self.force_calls += 1

    def force_poll(self) -> None:
        self.force_poll_calls += 1

    def last_successful_sync_at_monotonic(self):
        return self._last_mono

    def set_last_mono(self, v):
        self._last_mono = v


def _make_app_with_schedule(test, *, initial_enabled: bool = False):
    """Build an App with the schedule trio wired."""
    mono = _FakeMonotonic()
    app, clock, camera = _make_app(test, now_monotonic=mono)
    tc = TrustedClock(now_monotonic=mono)
    prober = _SpyProber()
    store = ScheduleStateStore(app.store.path.parent / "schedule_state.json")
    # The evaluator only ticks when explicitly called in these tests.
    ev = ScheduleEvaluator(
        scheduler=app.scheduler,
        trusted_clock=tc,
        configs_provider=app.snapshot_configs,
        schedule_enabled_provider=app.is_schedule_enabled,
        active_config_name_provider=app.active_config_name,
    )
    dirty = threading.Event()
    app.bind_schedule(
        trusted_clock=tc,
        time_sync_prober=prober,
        schedule_evaluator=ev,
        schedule_store=store,
        initial_enabled=initial_enabled,
        dirty_event=dirty,
    )
    return app, tc, prober, ev, store, dirty, mono


class TestScheduleBinding(unittest.TestCase):
    def test_bind_schedule_populates_all_attrs(self):
        app, tc, prober, ev, store, dirty, _ = _make_app_with_schedule(self)
        self.assertIs(app.trusted_clock, tc)
        self.assertIs(app.time_sync_prober, prober)
        self.assertIs(app.schedule_evaluator, ev)
        self.assertIs(app.schedule_store, store)
        self.assertFalse(app.schedule_enabled)

    def test_snapshot_configs_returns_copy(self):
        app, *_ = _make_app_with_schedule(self)
        app.configs = [A, B]
        snap = app.snapshot_configs()
        self.assertEqual([c.name for c in snap], ["A", "B"])
        # Mutating the copy must not affect the App.
        snap.append(B)
        self.assertEqual(len(app.configs), 2)

    def test_is_schedule_enabled_returns_state(self):
        app, *_ = _make_app_with_schedule(self, initial_enabled=True)
        self.assertTrue(app.is_schedule_enabled())

    def test_active_config_name_idle_returns_none(self):
        app, *_ = _make_app_with_schedule(self)
        self.assertIsNone(app.active_config_name())

    def test_active_config_name_running_returns_name(self):
        app, *_ = _make_app_with_schedule(self)
        app.scheduler.cmd_start(A)
        self.assertEqual(app.active_config_name(), "A")


class TestToggleSchedule(unittest.TestCase):
    def test_toggle_flips_flag(self):
        app, *_, dirty, _ = _make_app_with_schedule(self)
        self.assertFalse(app.schedule_enabled)
        app.toggle_schedule()
        self.assertTrue(app.schedule_enabled)
        app.toggle_schedule()
        self.assertFalse(app.schedule_enabled)

    def test_toggle_persists(self):
        app, _, _, _, store, _, _ = _make_app_with_schedule(self)
        app.toggle_schedule()
        # Re-read from disk via a fresh store.
        fresh = ScheduleStateStore(store.path).load()
        self.assertTrue(fresh)

    def test_toggle_sets_dirty(self):
        app, *_, dirty, _ = _make_app_with_schedule(self)
        dirty.clear()
        app.toggle_schedule()
        self.assertTrue(dirty.is_set())


class TestForceTrustNextSync(unittest.TestCase):
    def test_arms_flag_before_request_sync(self):
        app, tc, prober, *_ = _make_app_with_schedule(self)
        # Use a side-effect tracker on the trusted clock.
        original = tc.force_trust_next_sync
        order: list[str] = []

        def tc_call() -> None:
            order.append("tc")
            original()

        tc.force_trust_next_sync = tc_call  # type: ignore[assignment]

        def prober_call() -> None:
            order.append("prober")

        prober.request_force_sync = prober_call  # type: ignore[assignment]
        app.force_trust_next_sync()
        self.assertEqual(order, ["tc", "prober"])


class TestOnSyncObserved(unittest.TestCase):
    def test_first_sync_resets_frontier(self):
        app, tc, prober, ev, *_ = _make_app_with_schedule(self)
        # Force the evaluator to have a seeded frontier.
        ev._last_evaluated_at = datetime(2026, 1, 1, 0, 0, 0)
        app.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        self.assertIsNone(ev._last_evaluated_at)

    def test_accepted_does_not_reset_frontier(self):
        app, tc, prober, ev, *_ = _make_app_with_schedule(self)
        # Plant a baseline so the next sync is evaluated against the envelope.
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # Seed a frontier.
        seeded = datetime(2026, 5, 29, 11, 0, 0)
        ev._last_evaluated_at = seeded
        # A second sync 1 second later is ACCEPTED.
        app.on_sync_observed(datetime(2026, 5, 29, 12, 0, 1))
        self.assertEqual(ev._last_evaluated_at, seeded)


class TestSetManualTime(unittest.TestCase):
    def test_happy_path_runs_timedatectl_and_anchors_baseline(self):
        app, tc, prober, ev, *_ = _make_app_with_schedule(self)
        calls: list[list[str]] = []
        app._timedatectl_runner = lambda cmd: calls.append(cmd)
        app.set_manual_time(datetime(2026, 8, 12, 11, 33, 23))
        self.assertEqual(
            calls, [["timedatectl", "set-time", "2026-08-12 11:33:23"]],
        )
        self.assertTrue(tc.has_baseline)

    def test_failure_path_leaves_clock_untouched(self):
        app, tc, prober, ev, *_ = _make_app_with_schedule(self)
        def boom(cmd):
            raise RuntimeError("simulated subprocess failure")
        app._timedatectl_runner = boom
        app.set_manual_time(datetime(2026, 8, 12, 11, 33, 23))
        # Trusted clock was never anchored.
        self.assertFalse(tc.has_baseline)


class TestComputeScheduleIndicator(unittest.TestCase):
    def test_off_when_disabled(self):
        app, tc, *_ = _make_app_with_schedule(self)
        self.assertEqual(
            app._compute_schedule_indicator(), ScheduleIndicator.OFF,
        )

    def test_red_when_enabled_no_baseline(self):
        app, tc, *_ = _make_app_with_schedule(self, initial_enabled=True)
        self.assertEqual(
            app._compute_schedule_indicator(), ScheduleIndicator.RED,
        )

    def test_yellow_when_glitched(self):
        app, tc, prober, *_ = _make_app_with_schedule(self, initial_enabled=True)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # Force a glitched state.
        tc._is_glitched = True
        self.assertEqual(
            app._compute_schedule_indicator(), ScheduleIndicator.YELLOW,
        )

    def test_green_when_fresh(self):
        app, tc, prober, _ev, _store, _dirty, mono = _make_app_with_schedule(
            self, initial_enabled=True,
        )
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        prober.set_last_mono(mono())   # very fresh — same monotonic as App
        self.assertEqual(
            app._compute_schedule_indicator(), ScheduleIndicator.GREEN,
        )

    def test_yellow_when_stale(self):
        app, tc, prober, _ev, _store, _dirty, mono = _make_app_with_schedule(
            self, initial_enabled=True,
        )
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        prober.set_last_mono(mono() - 8 * 3600)   # 8h ago — App's injected clock
        self.assertEqual(
            app._compute_schedule_indicator(), ScheduleIndicator.YELLOW,
        )


class TestDispatchMainSchedule(unittest.TestCase):
    def test_toggle_schedule_action_flips_flag(self):
        app, *_ = _make_app_with_schedule(self)
        app._dispatch_main(MainActionResult(MainAction.TOGGLE_SCHEDULE))
        self.assertTrue(app.schedule_enabled)

    def test_open_time_setup_action_transitions_and_creates_ix(self):
        app, *_ = _make_app_with_schedule(self)
        app._dispatch_main(MainActionResult(MainAction.OPEN_TIME_SETUP))
        self.assertEqual(app.state, AppState.TIME_SETUP)
        self.assertIsNotNone(app.time_setup_ix)
        self.assertEqual(app.time_setup_ix.cursor, 0)


class TestDispatchTimeSetup(unittest.TestCase):
    def test_force_ntp_sync_calls_force_trust_and_returns_to_main(self):
        """Addendum A1: with a synchronous spawner, the worker runs
        inline and the post-dispatch state matches the worker-done
        state — MAIN, no menu, force calls observed."""
        app, tc, prober, *_ = _make_app_with_schedule(self)
        app._sync_worker_spawner = lambda fn: fn()
        app.time_setup_ix = __import__(
            "fp_lapse.ui.time_setup_menu", fromlist=["TimeSetupMenuInteraction"],
        ).TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        app._dispatch_time_setup(TimeSetupMenuAction.FORCE_NTP_SYNC)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.time_setup_ix)
        self.assertEqual(prober.force_calls, 1)
        self.assertEqual(prober.force_poll_calls, 1)
        self.assertFalse(app._syncing)

    def test_set_manually_opens_picker_with_system_clock_target(self):
        app, tc, *_ = _make_app_with_schedule(self)
        from fp_lapse.ui.time_setup_menu import TimeSetupMenuInteraction
        app.time_setup_ix = TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        app._dispatch_time_setup(TimeSetupMenuAction.SET_MANUALLY)
        self.assertEqual(app.state, AppState.PICKER)
        self.assertIsNotNone(app.picker_ix)
        self.assertEqual(app.picker_ix.target_field, "system_clock")
        self.assertIsNone(app.time_setup_ix)

    def test_cancel_returns_to_main_with_no_side_effects(self):
        app, tc, prober, *_ = _make_app_with_schedule(self)
        from fp_lapse.ui.time_setup_menu import TimeSetupMenuInteraction
        app.time_setup_ix = TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        app._dispatch_time_setup(TimeSetupMenuAction.CANCEL)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.time_setup_ix)
        self.assertIsNone(app.picker_ix)
        self.assertEqual(prober.force_calls, 0)


class TestSyncWorker(unittest.TestCase):
    """Addendum A1: force-NTP-sync UI feedback (async worker)."""

    def _setup_app_with_deferred_spawner(self):
        """Spawner that captures `fn` instead of running it. Lets a test
        inspect the half-state (sync in flight, menu still open) and
        then invoke `fn()` manually to observe the done-state."""
        app, tc, prober, *_ = _make_app_with_schedule(self)
        captured = {"fn": None}
        app._sync_worker_spawner = lambda fn: captured.update(fn=fn)
        from fp_lapse.ui.time_setup_menu import TimeSetupMenuInteraction
        app.time_setup_ix = TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        return app, tc, prober, captured

    def test_dispatch_returns_immediately_with_menu_still_open(self):
        """Pre-fix bug: UI froze until the subprocess returned. Post-
        fix: dispatch sets _syncing and returns; menu stays open."""
        app, tc, prober, captured = self._setup_app_with_deferred_spawner()
        app._dispatch_time_setup(TimeSetupMenuAction.FORCE_NTP_SYNC)
        # Worker has NOT run yet — it was deferred.
        self.assertEqual(app.state, AppState.TIME_SETUP)
        self.assertIsNotNone(app.time_setup_ix)
        self.assertTrue(app._syncing)
        self.assertEqual(prober.force_calls, 0)
        # Run the deferred worker — now the post-completion state applies.
        captured["fn"]()
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.time_setup_ix)
        self.assertFalse(app._syncing)
        self.assertEqual(prober.force_calls, 1)
        self.assertEqual(prober.force_poll_calls, 1)

    def test_buttons_are_inert_while_syncing(self):
        """Addendum A1: all buttons in TIME_SETUP no-op during sync."""
        from fp_lapse.buttons.iface import ButtonId
        app, tc, prober, captured = self._setup_app_with_deferred_spawner()
        app._dispatch_time_setup(TimeSetupMenuAction.FORCE_NTP_SYNC)
        cursor_before = app.time_setup_ix.cursor
        # All button presses should leave state untouched.
        for bid in (ButtonId.UP, ButtonId.DOWN, ButtonId.OK,
                    ButtonId.BACK, ButtonId.LEFT, ButtonId.RIGHT):
            app.on_press(bid)
        self.assertEqual(app.state, AppState.TIME_SETUP)
        self.assertTrue(app._syncing)
        self.assertEqual(app.time_setup_ix.cursor, cursor_before)
        # Sanity: the prober was NOT triggered again.
        self.assertEqual(prober.force_calls, 0)

    def test_start_sync_worker_is_idempotent_when_already_syncing(self):
        app, *_ = _make_app_with_schedule(self)
        spawn_calls = []
        app._sync_worker_spawner = lambda fn: spawn_calls.append(fn)
        app._start_sync_worker()
        self.assertEqual(len(spawn_calls), 1)
        # Second call while still syncing must be a no-op.
        app._start_sync_worker()
        self.assertEqual(len(spawn_calls), 1)

    def test_compute_syncing_dots_returns_none_when_idle(self):
        app, *_ = _make_app_with_schedule(self)
        self.assertFalse(app._syncing)
        self.assertIsNone(app._compute_syncing_dots())

    def test_compute_syncing_dots_cycles_one_two_three(self):
        """At ~2 Hz: phase rotates 1 → 2 → 3 → 1 → … (modulo 3)."""
        app, *_ = _make_app_with_schedule(self)
        app._syncing = True
        # Drive monotonic explicitly to anchor the start time.
        import time as _time
        app._syncing_started_mono = _time.monotonic()
        # Snap the started time so deltas are predictable across runs.
        anchor = app._syncing_started_mono
        # At Δ = 0, formula = int(0) + 1 = 1.
        # At Δ = 0.6 s, formula = int(1.2) + 1 = 2.
        # At Δ = 1.1 s, formula = int(2.2) + 1 = 3.
        # At Δ = 1.6 s, formula = int(3.2) % 3 + 1 = 1.
        for delta, expected in [
            (0.00, 1), (0.20, 1),
            (0.60, 2), (0.90, 2),
            (1.10, 3), (1.40, 3),
            (1.60, 1), (2.20, 2),
        ]:
            app._syncing_started_mono = anchor - delta
            self.assertEqual(
                app._compute_syncing_dots(), expected,
                f"delta={delta}",
            )

    def test_worker_clears_syncing_even_on_runner_failure(self):
        """request_force_sync raising must not leave _syncing pinned."""
        app, tc, prober, *_ = _make_app_with_schedule(self)
        app._sync_worker_spawner = lambda fn: fn()
        def boom() -> None:
            raise RuntimeError("simulated subprocess failure")
        prober.request_force_sync = boom  # type: ignore[method-assign]
        from fp_lapse.ui.time_setup_menu import TimeSetupMenuInteraction
        app.time_setup_ix = TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        app._dispatch_time_setup(TimeSetupMenuAction.FORCE_NTP_SYNC)
        self.assertFalse(app._syncing)
        self.assertEqual(app.state, AppState.MAIN)

    def test_worker_logs_warning_on_timeout(self):
        """Addendum A1: a hung subprocess is bounded by the 10 s
        watchdog. We exercise the timeout path by patching the
        deadline check to always return True."""
        app, tc, prober, *_ = _make_app_with_schedule(self)
        app._sync_worker_spawner = lambda fn: fn()
        # Force the watchdog to fire on every check.
        app._sync_timed_out = lambda: True  # type: ignore[assignment]
        from fp_lapse.ui.time_setup_menu import TimeSetupMenuInteraction
        app.time_setup_ix = TimeSetupMenuInteraction()
        app.state = AppState.TIME_SETUP
        with self.assertLogs("fp_lapse.app", level="WARNING") as captured:
            app._dispatch_time_setup(TimeSetupMenuAction.FORCE_NTP_SYNC)
        self.assertFalse(app._syncing)
        self.assertEqual(app.state, AppState.MAIN)
        # Watchdog skipped the prober calls because the deadline was
        # already exceeded on entry to each step.
        self.assertEqual(prober.force_calls, 0)
        self.assertEqual(prober.force_poll_calls, 0)
        self.assertTrue(any("budget" in line for line in captured.output))


class TestPickerDispatch(unittest.TestCase):
    def test_system_clock_save_runs_set_manual_time(self):
        app, tc, *_ = _make_app_with_schedule(self)
        from fp_lapse.ui.picker_datetime import DateTimePickerInteraction
        app.picker_ix = DateTimePickerInteraction(
            target_field="system_clock",
            initial_value=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
        )
        app.state = AppState.PICKER
        calls: list[list[str]] = []
        app._timedatectl_runner = lambda cmd: calls.append(cmd)
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.picker_ix)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["timedatectl", "set-time"])

    def test_system_clock_cancel_returns_to_main(self):
        app, tc, *_ = _make_app_with_schedule(self)
        from fp_lapse.ui.picker_datetime import DateTimePickerInteraction
        app.picker_ix = DateTimePickerInteraction(target_field="system_clock")
        app.state = AppState.PICKER
        called = False
        def boom(cmd):
            nonlocal called
            called = True
        app._timedatectl_runner = boom
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.MAIN)
        self.assertIsNone(app.picker_ix)
        self.assertFalse(called)

    def test_start_picker_save_writes_into_draft(self):
        from dataclasses import replace
        from fp_lapse.ui import EditScreenInteraction
        from fp_lapse.ui.picker_datetime import DateTimePickerInteraction

        cfg = TimelapseConfig(
            "X", 10.0,
            (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        app, *_ = _make_app_with_schedule(self)
        app.edit_ix = EditScreenInteraction(cfg)
        app.state = AppState.PICKER
        app.picker_ix = DateTimePickerInteraction(
            target_field="start",
            initial_value=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
        )
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.EDIT)
        self.assertIsNotNone(app.edit_ix.draft.start)
        self.assertEqual(
            app.edit_ix.draft.start.date, date_t(2026, 8, 12),
        )
        self.assertIsNone(app.picker_ix)

    def test_start_picker_cancel_returns_to_edit(self):
        from fp_lapse.ui import EditScreenInteraction
        from fp_lapse.ui.picker_datetime import DateTimePickerInteraction
        cfg = TimelapseConfig(
            "X", 10.0,
            (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        app, *_ = _make_app_with_schedule(self)
        app.edit_ix = EditScreenInteraction(cfg)
        app.state = AppState.PICKER
        app.picker_ix = DateTimePickerInteraction(target_field="start")
        app.on_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.EDIT)
        self.assertIsNone(app.picker_ix)


class TestPastDateWarning(unittest.TestCase):
    def test_no_warning_without_dates(self):
        from fp_lapse.app import _past_date_warning
        cfg = TimelapseConfig("X", 10.0, (Shot(shutter=1 / 500, iso=200),))
        self.assertIsNone(_past_date_warning(cfg))

    def test_no_warning_with_future_date(self):
        from fp_lapse.app import _past_date_warning
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200),),
            start=ScheduledMoment(
                time=time_t(11, 0, 0), date=date_t(2099, 1, 1),
            ),
        )
        self.assertIsNone(_past_date_warning(cfg))

    def test_warning_with_past_start(self):
        from fp_lapse.app import _past_date_warning
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200),),
            start=ScheduledMoment(
                time=time_t(11, 0, 0), date=date_t(2000, 1, 1),
            ),
        )
        w = _past_date_warning(cfg)
        self.assertIsNotNone(w)
        self.assertIn("start", w)


class TestMainScreenWallClockFromTrustedClock(unittest.TestCase):
    def test_uses_trusted_clock_when_baseline(self):
        app, tc, *_ = _make_app_with_schedule(self)
        tc.on_sync_observed(datetime(2030, 1, 1, 12, 34, 56))
        # The render path picks up the trusted-clock time.
        img1 = app.render()
        # And a render without baseline differs (we will reset state).
        # Just confirm the trusted-clock now() is what feeds the
        # status bar by reading _render_main_screen indirectly via
        # the UIState wall_clock_str (smoke).
        from datetime import datetime as _dt
        from fp_lapse.ui import UIState  # noqa: F401
        # If the trusted clock baseline is honored, the render must
        # NOT match the rendering after we clear the baseline.
        tc._baseline_mono = None
        tc._baseline_wall = None
        img2 = app.render()
        self.assertNotEqual(img1.tobytes(), img2.tobytes())


class TestNamingHelpers(unittest.TestCase):
    def test_new_config_skips_existing_names(self):
        app, _, _ = _make_app(self, initial_configs=(
            TimelapseConfig("Config 1", 10.0, (Shot(shutter=1/500, iso=200),)),
        ))
        new = app._new_config()
        self.assertEqual(new.name, "Config 2")

    def test_copy_name_falls_back_to_numbered(self):
        # Pre-create "A (copy)" so the simple suffix collides.
        app, _, _ = _make_app(self, initial_configs=(
            TimelapseConfig("A", 10.0, (Shot(shutter=1/500, iso=200),)),
            TimelapseConfig("A (copy)", 10.0, (Shot(shutter=1/500, iso=200),)),
        ))
        name = app._next_copy_name("A")
        self.assertEqual(name, "A (copy 2)")


if __name__ == "__main__":
    unittest.main()
