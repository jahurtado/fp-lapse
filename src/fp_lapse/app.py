"""Orchestrator: screen state machine + dispatch to engine and store.

States: `MAIN / EDIT / MANAGE`, plus four confirmation overlays —
`OVERLAY_STOP` (BACK while running), `OVERLAY_SAVE` (OK in edit),
`OVERLAY_DISCARD` (BACK in edit with pending changes), and
`OVERLAY_DELETE` (Manage → Delete). Each overlay's text comes from a
factory in `ui.overlays`; `handle_overlay_button` maps OK to True and
BACK to False.

Threading model: `App` is shared between several threads — the UI
thread reads its state via `render()`, GPIO callbacks call
`on_press/on_release/on_long_press`, and the engine scheduler runs in
its own thread. All these acquire `self.lock` (an RLock) at entry.
Engine commands (start / stop / switch) are routed through
`self.scheduler.cmd_*()`, which has its own lock and ordering: the
caller blocks until any in-flight bracket finishes (spec §5.3).

Persistence: the App is the only writer of `runtime/configs.json`.
Operations that change the list (`_commit_edit`, `_duplicate`,
`_delete`) trigger `store.save()` after updating the in-memory list.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime
from enum import Enum
from typing import List, Optional

from PIL import Image

from .camera import Camera
from .configs import (
    ConfigStore,
    ConfigValidationError,
    MAX_NAME_LENGTH,
    Shot,
    TimelapseConfig,
)
from .engine import EngineState
from .ui import (
    EditAction,
    EditScreen,
    EditScreenInteraction,
    EditState,
    MainAction,
    MainActionResult,
    MainScreen,
    MainScreenInteraction,
    ManageMenuAction,
    ManageMenuInteraction,
    ManageMenuState,
    UIState,
    handle_overlay_button,
    render_manage_menu,
    render_overlay,
    delete_confirm,
    discard_changes,
    save_confirm,
    stop_confirm,
)

logger = logging.getLogger(__name__)


class AppState(str, Enum):
    MAIN = "main"
    EDIT = "edit"
    MANAGE = "manage"
    OVERLAY_STOP = "overlay_stop"
    OVERLAY_SAVE = "overlay_save"
    OVERLAY_DISCARD = "overlay_discard"  # BACK in edit with pending changes
    OVERLAY_DELETE = "overlay_delete"    # Manage → Delete


class App:
    def __init__(
        self,
        *,
        scheduler,
        store: ConfigStore,
        camera: Camera,
    ) -> None:
        self.scheduler = scheduler
        self.store = store
        self.camera = camera

        # One lock for everything App-side. Held by:
        #   - GPIO callbacks (`on_press` / `on_release` / `on_long_press`)
        #   - UI thread during `render()`
        # The engine scheduler thread has its own internal lock and does
        # NOT take `app.lock` — it only mutates engine state, which the
        # UI reads via atomic primitive accesses (GIL-safe).
        self.lock = threading.RLock()

        self.configs: List[TimelapseConfig] = list(self.store.load())
        self._configs_reset = self.store.was_reset_from_corruption

        self.main_ix = MainScreenInteraction()
        self.edit_ix: Optional[EditScreenInteraction] = None
        self.manage_ix = ManageMenuInteraction()
        self._manage_target_name: Optional[str] = None
        self.state: AppState = AppState.MAIN

        self._main_screen = MainScreen()
        self._edit_screen = EditScreen()

    @property
    def engine(self):
        """The underlying engine, exposed for status reads only.

        Mutations must go through `self.scheduler.cmd_*()` so they
        serialize with the scheduler thread's `engine.tick()` calls.
        """
        return self.scheduler.engine

    # -- Main loop hooks -------------------------------------------------

    def on_press(self, button) -> None:
        with self.lock:
            if self.state == AppState.MAIN:
                r = self.main_ix.on_press(
                    button,
                    configs=tuple(self.configs),
                    engine_state=self.engine.state,
                )
                if r is not None:
                    self._dispatch_main(r)
            elif self.state == AppState.EDIT:
                r = self.edit_ix.on_press(button) if self.edit_ix else None
                if r is not None:
                    self._dispatch_edit(r)
            elif self.state == AppState.MANAGE:
                r = self.manage_ix.on_press(button)
                if r is not None:
                    self._dispatch_manage(r)
            elif self.state == AppState.OVERLAY_STOP:
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_overlay_stop(r)
            elif self.state == AppState.OVERLAY_SAVE:
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_overlay_save(r)
            elif self.state == AppState.OVERLAY_DISCARD:
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_overlay_discard(r)
            elif self.state == AppState.OVERLAY_DELETE:
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_overlay_delete(r)

    def on_release(self, button) -> None:
        with self.lock:
            if self.state == AppState.MAIN:
                active_name = (
                    self.engine.active_config.name
                    if self.engine.active_config is not None else None
                )
                r = self.main_ix.on_release(
                    button,
                    configs=tuple(self.configs),
                    engine_state=self.engine.state,
                    active_config_name=active_name,
                )
                if r is not None:
                    self._dispatch_main(r)

    def on_long_press(self, button) -> None:
        """Long-press hook. Currently only the main screen consumes it."""
        with self.lock:
            if self.state == AppState.MAIN:
                r = self.main_ix.on_long_press(
                    button,
                    configs=tuple(self.configs),
                )
                if r is not None:
                    self._dispatch_main(r)

    # -- Rendering -------------------------------------------------------

    def render(self) -> Image.Image:
        with self.lock:
            main_img = self._render_main_screen()
            if self.state == AppState.MAIN:
                return main_img
            if self.state == AppState.EDIT and self.edit_ix is not None:
                return self._render_edit_image()
            if self.state == AppState.MANAGE:
                return render_manage_menu(
                    main_img,
                    ManageMenuState(
                        self._manage_target_name or "?",
                        self.manage_ix.cursor,
                    ),
                )
            if self.state == AppState.OVERLAY_STOP:
                return render_overlay(main_img, stop_confirm())
            if self.state == AppState.OVERLAY_SAVE and self.edit_ix is not None:
                return render_overlay(self._render_edit_image(), save_confirm())
            if self.state == AppState.OVERLAY_DISCARD and self.edit_ix is not None:
                return render_overlay(self._render_edit_image(), discard_changes())
            if self.state == AppState.OVERLAY_DELETE:
                # Overlay sits on top of the manage menu the user came from.
                manage_img = render_manage_menu(
                    main_img,
                    ManageMenuState(
                        self._manage_target_name or "?",
                        self.manage_ix.cursor,
                    ),
                )
                return render_overlay(
                    manage_img,
                    delete_confirm(self._manage_target_name or "?"),
                )
            return main_img

    def _render_edit_image(self) -> Image.Image:
        assert self.edit_ix is not None
        return self._edit_screen.render(EditState(
            cfg=self.edit_ix.draft,
            field_cursor=self.edit_ix.field_cursor,
            scroll_offset=self.edit_ix.scroll_offset,
        ))

    # Spec §6.1: persistent "CAMERA NOT RESPONDING" once we cross this
    # threshold of consecutive failed shots. Resets automatically on
    # the next success because the engine clears its counter.
    _CAMERA_DOWN_THRESHOLD: int = 5

    def _render_main_screen(self) -> Image.Image:
        status = self.engine.status()
        return self._main_screen.render(UIState(
            configs=tuple(self.configs),
            cursor=self.main_ix.cursor,
            engine_state=status.state,
            active_config_name=status.active_config_name,
            shots_taken=status.shots_taken,
            seconds_to_next_shot=status.seconds_to_next_shot,
            skips=status.skips,
            camera_connected=self.camera.is_connected(),
            wall_clock_str=datetime.now().strftime("%H:%M:%S"),
            camera_not_responding=(
                status.consecutive_failures >= self._CAMERA_DOWN_THRESHOLD
            ),
            configs_reset=self._configs_reset,
            camera_model_label=self._camera_model_label(),
            dial_mismatch=self._camera_dial_mismatch(),
        ))

    def _camera_model_label(self) -> str:
        """Live status-bar label for the camera ("fp" / "D5600").

        The `CameraProxy` exposes `model_label()` (cached, I/O-free). Plain
        adapters / `MockCamera` lack it, so we default to "fp".
        """
        fn = getattr(self.camera, "model_label", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
        return "fp"

    def _camera_dial_mismatch(self) -> bool:
        """Whether the live camera's exposure dial is in the wrong mode.

        The `CameraProxy` exposes `dial_mismatch()` (delegating to the
        Nikon adapter). Adapters without the concept report False.
        """
        fn = getattr(self.camera, "dial_mismatch", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        return False

    # -- Dispatch helpers ------------------------------------------------

    def _dispatch_main(self, r: MainActionResult) -> None:
        if r.kind == MainAction.START and r.cfg is not None:
            self.scheduler.cmd_start(r.cfg)
        elif r.kind == MainAction.SWITCH and r.cfg is not None:
            self.scheduler.cmd_switch(r.cfg)
        elif r.kind == MainAction.STOP_CONFIRM:
            self.state = AppState.OVERLAY_STOP
        elif r.kind == MainAction.OPEN_MANAGE and r.cfg is not None:
            self._manage_target_name = r.cfg.name
            self.manage_ix.reset()
            self.main_ix.reset_input()
            self.state = AppState.MANAGE
        elif r.kind == MainAction.OPEN_EDIT_NEW:
            self.edit_ix = EditScreenInteraction(self._new_config())
            self.main_ix.reset_input()
            self.state = AppState.EDIT

    def _dispatch_edit(self, action: EditAction) -> None:
        if action == EditAction.SAVE and self.edit_ix is not None:
            # OK in edit always asks for confirmation, regardless of
            # whether there are changes or it's a brand-new config —
            # saving is irreversible and the spec favours safety over
            # speed.
            self.state = AppState.OVERLAY_SAVE
            return
        if (
            action == EditAction.BACK
            and self.edit_ix is not None
            and self.edit_ix.is_dirty
        ):
            # §7.4: pending changes → confirm before discarding.
            self.state = AppState.OVERLAY_DISCARD
            return
        # No pending changes (or no edit context) — leave directly.
        self.edit_ix = None
        self.state = AppState.MAIN

    def _dispatch_overlay_discard(self, ok: bool) -> None:
        if not ok:
            # BACK / Cancel: return to edit, keep the draft.
            self.state = AppState.EDIT
            return
        # Confirmed: drop the draft and go back to MAIN.
        self.edit_ix = None
        self.state = AppState.MAIN

    def _dispatch_overlay_save(self, ok: bool) -> None:
        if not ok:
            # BACK / Cancel: return to edit without saving.
            self.state = AppState.EDIT
            return
        if self.edit_ix is not None:
            self._commit_edit(self.edit_ix.draft, self.edit_ix.original)
        self.edit_ix = None
        self.state = AppState.MAIN

    def _dispatch_manage(self, action: ManageMenuAction) -> None:
        target = self._find_config(self._manage_target_name)
        if action == ManageMenuAction.EDIT and target is not None:
            self.edit_ix = EditScreenInteraction(target)
            self.state = AppState.EDIT
            return
        if action == ManageMenuAction.DUPLICATE and target is not None:
            self._duplicate(target)
            self.state = AppState.MAIN
            return
        if action == ManageMenuAction.DELETE and target is not None:
            # §7.4: confirm before deleting. The Delete confirmation
            # also acts as a Stop when the deleted config is running
            # (§4.3 / §7.5) — that's handled in
            # `_dispatch_overlay_delete`.
            self.state = AppState.OVERLAY_DELETE
            return
        # CANCEL or BACK: nothing to do besides returning to MAIN.
        self.state = AppState.MAIN

    def _dispatch_overlay_delete(self, ok: bool) -> None:
        if not ok:
            # Cancel: back to the manage menu the user came from.
            self.state = AppState.MANAGE
            return
        target = self._find_config(self._manage_target_name)
        if target is not None:
            self._delete(target)
        self.state = AppState.MAIN

    def _dispatch_overlay_stop(self, ok: bool) -> None:
        if ok:
            self.scheduler.cmd_stop()
        self.state = AppState.MAIN

    # -- Mutations -------------------------------------------------------

    def _commit_edit(
        self,
        draft: TimelapseConfig,
        original: TimelapseConfig,
    ) -> None:
        # Replace the config with the same original name; if none exists
        # ("+ New" case), append it.
        idx = next(
            (i for i, c in enumerate(self.configs) if c.name == original.name),
            None,
        )
        new_list = list(self.configs)
        if idx is None:
            new_list.append(draft)
        else:
            new_list[idx] = draft
        try:
            self.store.save(new_list)
            self.configs = new_list
            # §6.3: a successful save clears the CONFIGS RESET banner —
            # the user has just created/edited something on top of the
            # rescued-empty state.
            self._configs_reset = False
        except ConfigValidationError as e:
            logger.error("config save rejected: %s", e)

    def _duplicate(self, cfg: TimelapseConfig) -> None:
        self._configs_reset = False  # see §6.3 note in _commit_edit
        new_name = self._next_copy_name(cfg.name)
        new_cfg = replace(cfg, name=new_name)
        idx = next(
            (i for i, c in enumerate(self.configs) if c.name == cfg.name),
            None,
        )
        new_list = list(self.configs)
        if idx is None:
            new_list.append(new_cfg)
        else:
            new_list.insert(idx + 1, new_cfg)
        try:
            self.store.save(new_list)
            self.configs = new_list
            # Position the cursor on the freshly created copy.
            self.main_ix.cursor = (idx + 1) if idx is not None else len(new_list) - 1
        except ConfigValidationError as e:
            logger.error("duplicate rejected: %s", e)

    def _delete(self, cfg: TimelapseConfig) -> None:
        new_list = [c for c in self.configs if c.name != cfg.name]
        # If the deleted config was running, stop the engine
        # (§4.3 / §7.5): the Delete confirmation already acts as the Stop.
        if (
            self.engine.state == EngineState.RUNNING
            and self.engine.active_config is not None
            and self.engine.active_config.name == cfg.name
        ):
            self.scheduler.cmd_stop()
        try:
            self.store.save(new_list)
            self.configs = new_list
            self.main_ix.cursor = max(
                0, min(self.main_ix.cursor, len(new_list)),
            )
        except ConfigValidationError as e:
            logger.error("delete rejected: %s", e)

    def _new_config(self) -> TimelapseConfig:
        existing = {c.name for c in self.configs}
        n = 1
        while f"Config {n}" in existing:
            n += 1
        return TimelapseConfig(
            name=f"Config {n}",
            interval_s=10.0,
            shots=(Shot(shutter=1 / 30, iso=200, aperture=None),),
        )

    def _next_copy_name(self, base: str) -> str:
        existing = {c.name for c in self.configs}
        candidate = f"{base} (copy)"
        if candidate not in existing and len(candidate) <= MAX_NAME_LENGTH:
            return candidate
        for n in range(2, 100):
            suffix = f" (copy {n})"
            allowed_base = max(1, MAX_NAME_LENGTH - len(suffix))
            candidate = f"{base[:allowed_base]}{suffix}"
            if candidate not in existing and len(candidate) <= MAX_NAME_LENGTH:
                return candidate
        return base  # validation will reject this if we ever get here

    def _find_config(self, name: Optional[str]) -> Optional[TimelapseConfig]:
        if name is None:
            return None
        return next((c for c in self.configs if c.name == name), None)
