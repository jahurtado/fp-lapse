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

from fp_lapse.app import App, AppState  # noqa: E402
from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.camera import MockCamera  # noqa: E402
from fp_lapse.configs import ConfigStore, Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import Engine, EngineState  # noqa: E402


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


def _make_app(test: unittest.TestCase, initial_configs=()):
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
    app = App(scheduler=scheduler, store=store, camera=camera)
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
        app.on_press(ButtonId.OK)                              # OK → overlay
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        self.assertEqual(app.configs, [])                      # not yet persisted
        app.on_press(ButtonId.OK)                              # confirm
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual([c.name for c in app.configs], ["Config 1"])

    def test_new_back_in_overlay_returns_to_edit(self):
        """BACK en el overlay de save vuelve al editor; nada se persiste."""
        app, _, _ = _make_app(self)
        app.on_press(ButtonId.OK); app.on_release(ButtonId.OK)
        app.on_press(ButtonId.OK)
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
        app.on_press(ButtonId.OK)                   # request SAVE
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
        app.on_press(ButtonId.OK)                   # SAVE → OVERLAY_SAVE
        app.on_press(ButtonId.OK)                   # confirm
        self.assertEqual(app.state, AppState.MAIN)
        self.assertNotEqual(app.configs[0].interval_s, 10.0)

    def test_overlay_save_back_returns_to_edit(self):
        app, _, _ = _make_app(self, initial_configs=(A,))
        from fp_lapse.ui import EditScreenInteraction
        app.edit_ix = EditScreenInteraction(A)
        app.state = AppState.EDIT
        app.edit_ix.on_press(ButtonId.DOWN)
        app.edit_ix.on_press(ButtonId.RIGHT)
        app.on_press(ButtonId.OK)                   # SAVE → OVERLAY_SAVE
        app.on_press(ButtonId.BACK)                 # cancel
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
        app.on_press(ButtonId.OK)                              # OK → overlay
        self.assertEqual(app.state, AppState.OVERLAY_SAVE)
        app.on_press(ButtonId.OK)                              # confirm
        self.assertEqual(app.state, AppState.MAIN)
        self.assertEqual(app.configs[0].interval_s, 10.0)


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
