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
import subprocess
import threading
import time
from dataclasses import replace
from datetime import date as date_t, datetime
from enum import Enum
from typing import Callable, List, Optional

from PIL import Image

from .buttons.iface import ButtonId
from .camera import Camera
from .configs import (
    ConfigStore,
    ConfigValidationError,
    MAX_NAME_LENGTH,
    Shot,
    TimelapseConfig,
)
from .engine import EngineState
from .schedule import (
    SCHEDULE_STALE_THRESHOLD_S,
    ScheduleEvaluator,
    ScheduleStateStore,
    SyncOutcome,
    TimeSyncProber,
    TrustedClock,
)
from .schedule.moment import ScheduledMoment
from .shutdown import do_shutdown
from .net.nmcli import ConnectOutcome
from .ui import (
    DateTimePickerInteraction,
    EditAction,
    EditScreen,
    EditScreenInteraction,
    EditState,
    KeyboardAction,
    KeyboardInteraction,
    MainAction,
    MainActionResult,
    MainScreen,
    MainScreenInteraction,
    ManageMenuAction,
    ManageMenuInteraction,
    ManageMenuState,
    PickerAction,
    ScheduleIndicator,
    TimeSetupMenuAction,
    TimeSetupMenuInteraction,
    TimeSetupMenuState,
    UIState,
    WifiListAction,
    WifiListInteraction,
    WifiListState,
    WifiStatusState,
    handle_overlay_button,
    poweroff_confirm,
    render_datetime_picker,
    render_keyboard,
    render_manage_menu,
    render_overlay,
    render_powering_off,
    render_time_setup_menu,
    render_wifi_list,
    render_wifi_status,
    delete_confirm,
    discard_changes,
    save_confirm,
    stop_confirm,
    wifi_forget_confirm,
)

logger = logging.getLogger(__name__)


def _default_timedatectl_runner(cmd: list[str]) -> None:
    """Default runner for `timedatectl set-time …`.

    Runs the subprocess with a short timeout and raises `subprocess
    .CalledProcessError` on non-zero exit so `App.set_manual_time()`
    can log a WARNING and bail out without touching the trusted clock.
    """
    subprocess.run(cmd, check=True, timeout=5)


def _default_sync_worker_spawner(fn: Callable[[], None]) -> None:
    """Default spawner for the force-NTP-sync worker (addendum A1).

    Runs `fn` on a daemon thread so the UI thread never blocks on the
    `timedatectl` subprocess. Tests inject a synchronous spawner that
    runs `fn()` inline so the worker behaviour is deterministic.
    """
    threading.Thread(target=fn, daemon=True, name="sync-worker").start()


def _default_wifi_worker_spawner(fn: Callable[[], None]) -> None:
    """Default spawner for the off-thread Wi-Fi connect/scan worker.

    Same idiom as `_default_sync_worker_spawner`: a daemon thread so the
    UI thread never blocks on the (up to 30 s) `nmcli` subprocess. Tests
    inject a synchronous spawner so the worker runs inline.
    """
    threading.Thread(target=fn, daemon=True, name="wifi-worker").start()


def _default_nmcli():
    """Pick the nmcli facade: mock on the Mac dev path, real on the Pi.

    Reuses the same gate as the camera layer (`FP_LAPSE_CAMERA == mock`
    or `sys.platform == "darwin"`) so the whole Wi-Fi flow is
    exercisable in the Tk dev harness with no hardware.
    """
    import os
    import sys

    from .net.nmcli import make_nmcli

    use_mock = (
        os.environ.get("FP_LAPSE_CAMERA", "").strip().lower() == "mock"
        or os.environ.get("FP_LAPSE_MOCK") == "1"
        or sys.platform == "darwin"
    )
    return make_nmcli(use_mock=use_mock, connect_delay_s=0.8 if use_mock else 0.0)


def _past_date_warning(cfg: TimelapseConfig) -> Optional[str]:
    """One-line warning when the draft has a past-dated start/end.

    Used by `OVERLAY_SAVE` (prd2.md §6.2). Returns `None` when there
    is no past date (the body of the save overlay stays empty).

    Text is constrained to fit inside the 240-px-wide modal dialog
    in mono-11 — the previous longer form ("Note: start date is in
    the past — won't fire.") rendered ~292 px and overflowed.
    """
    today = date_t.today()
    past_fields = [
        label for label, m in (("start", cfg.start), ("end", cfg.end))
        if m is not None and m.date is not None and m.date < today
    ]
    if not past_fields:
        return None
    return f"{past_fields[0].capitalize()} date past — won't fire"


class AppState(str, Enum):
    MAIN = "main"
    EDIT = "edit"
    MANAGE = "manage"
    OVERLAY_STOP = "overlay_stop"
    OVERLAY_SAVE = "overlay_save"
    OVERLAY_DISCARD = "overlay_discard"  # BACK in edit with pending changes
    OVERLAY_DELETE = "overlay_delete"    # Manage → Delete
    # prd2.md §6 — schedule UI states.
    TIME_SETUP = "time_setup"             # LEFT-press menu over main screen
    PICKER = "picker"                     # digit picker (from edit or time setup)
    # §7.8 — safe shutdown via BACK+OK chord.
    OVERLAY_POWEROFF = "overlay_poweroff"  # "Power off?" confirmation
    SHUTTING_DOWN = "shutting_down"        # phase 1 after confirm
    # wifi-manual-config — on-device Wi-Fi setup flow.
    WIFI_LIST = "wifi_list"                # network scan list
    WIFI_KEYBOARD = "wifi_keyboard"        # virtual keyboard (ssid or password)
    WIFI_STATUS = "wifi_status"            # connecting / connected / failed
    OVERLAY_WIFI_FORGET = "overlay_wifi_forget"  # "Forget network?" confirm


class App:
    def __init__(
        self,
        *,
        scheduler,
        store: ConfigStore,
        camera: Camera,
        now_monotonic: Callable[[], float] = time.monotonic,
        shutdown_action: Optional[Callable[[], None]] = None,
        nmcli=None,
        wifi_worker_spawner: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> None:
        self.scheduler = scheduler
        self.store = store
        self.camera = camera
        # Safe-shutdown invocation (§7.8). Injectable so tests can
        # replace `do_shutdown` (which Popens /sbin/shutdown) with a
        # spy. On Mac mock runs the default still fires but the
        # missing `/sbin/shutdown` is logged and swallowed — see
        # `shutdown.do_shutdown`.
        self._shutdown_action: Callable[[], None] = shutdown_action or do_shutdown
        # Previous AppState when the chord fired — restored if the
        # operator presses BACK on the "Power off?" overlay.
        self._prev_state_before_poweroff: Optional[AppState] = None
        # Monotonic clock used by the schedule-indicator staleness
        # check (and any future monotonic-time read inside `App`).
        # Injectable so unit tests can drive the staleness window
        # deterministically — matches the rest of the schedule stack
        # (TrustedClock, TimeSyncProber) which already inject their
        # own monotonic source.
        self._now_monotonic: Callable[[], float] = now_monotonic

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

        # Schedule wiring (prd2.md §7). Populated by `bind_schedule(...)`
        # from `__main__.main()`. Until then, the schedule indicator is
        # OFF, the wall clock reads from `datetime.now()`, and the LEFT
        # menu still opens but its actions are no-ops because there is
        # nothing to act on.
        self.trusted_clock: Optional[TrustedClock] = None
        self.time_sync_prober: Optional[TimeSyncProber] = None
        self.schedule_evaluator: Optional[ScheduleEvaluator] = None
        self.schedule_store: Optional[ScheduleStateStore] = None
        self.schedule_enabled: bool = False
        # Optional event the App sets after schedule-state mutations so
        # the UI loop re-renders immediately (same idiom as the camera
        # health thread). Wired by `bind_schedule(...)`.
        self._dirty_event: Optional[threading.Event] = None
        # Overlay interactions — exist only while their state is active.
        self.time_setup_ix: Optional[TimeSetupMenuInteraction] = None
        self.picker_ix: Optional[DateTimePickerInteraction] = None
        # Force-NTP-sync feedback (addendum A1). True while the daemon
        # worker spawned by `_dispatch_time_setup(FORCE_NTP_SYNC)` is
        # running. Drives the TIME SETUP menu's animated "Syncing..."
        # label and gates button presses in that state.
        self._syncing: bool = False
        # Monotonic timestamp when the current sync worker started.
        # Used both for the dots animation (modulo 3 phase) and for the
        # 10 s watchdog timeout.
        self._syncing_started_mono: float = 0.0
        # Indirection for spawning the worker so tests can intercept
        # the thread creation (e.g. run synchronously). Default: a
        # short-lived daemon thread.
        self._sync_worker_spawner: Callable[[Callable[[], None]], None] = (
            _default_sync_worker_spawner
        )
        # Indirection for `timedatectl set-time` so unit tests can spy
        # on the call. Default implementation shells out (the systemd
        # unit runs as root, so no `sudo` is needed).
        self._timedatectl_runner: Callable[[list[str]], None] = (
            _default_timedatectl_runner
        )

        # -- wifi-manual-config — on-device Wi-Fi setup flow ----------
        # Interactions exist only while their state is active.
        self.wifi_list_ix: Optional[WifiListInteraction] = None
        self.keyboard_ix: Optional[KeyboardInteraction] = None
        self.wifi_networks: tuple = ()
        self.wifi_status_state: Optional[WifiStatusState] = None
        # (ssid, secured, hidden) stashed between the list pick / SSID
        # keyboard and the connect worker.
        self._wifi_pending: Optional[tuple] = None
        self._wifi_forget_target: Optional[str] = None
        # True while a connect/scan worker is in flight — gates buttons
        # inert and drives the Connecting…/Scanning… dots animation
        # (mirrors `_syncing`).
        self._wifi_busy: bool = False
        self._wifi_busy_started_mono: float = 0.0
        # Injected nmcli facade (real on Pi, mock on Mac) + worker
        # spawner — both injectable so tests run the worker synchronously
        # and feed canned results.
        self._nmcli = nmcli if nmcli is not None else _default_nmcli()
        self._wifi_worker_spawner: Callable[[Callable[[], None]], None] = (
            wifi_worker_spawner or _default_wifi_worker_spawner
        )

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
            elif self.state == AppState.TIME_SETUP:
                if self.time_setup_ix is None:
                    return
                # Addendum A1: every button is inert while the sync
                # worker is in flight — including BACK. The menu
                # auto-closes when the worker finishes; the operator
                # only needs to wait.
                if self._syncing:
                    return
                r = self.time_setup_ix.on_press(button)
                if r is not None:
                    self._dispatch_time_setup(r)
            elif self.state == AppState.PICKER:
                if self.picker_ix is None:
                    return
                r = self.picker_ix.on_press(button)
                if r is PickerAction.SAVE:
                    self._dispatch_picker_save()
                elif r is PickerAction.CANCEL:
                    self._dispatch_picker_cancel()
            elif self.state == AppState.WIFI_LIST:
                if self.wifi_list_ix is None:
                    return
                # Scanning → every button inert (mirrors `_syncing`).
                if self._wifi_busy:
                    return
                r = self.wifi_list_ix.on_press(button)
                if r is not None:
                    self._dispatch_wifi_list(r)
            elif self.state == AppState.WIFI_KEYBOARD:
                if self.keyboard_ix is None:
                    return
                r = self.keyboard_ix.on_press(button)
                if r is KeyboardAction.DONE:
                    self._dispatch_keyboard_done()
                elif r is KeyboardAction.CANCEL:
                    self._dispatch_keyboard_cancel()
            elif self.state == AppState.WIFI_STATUS:
                # Connecting → inert; once settled OK/BACK navigate.
                if self._wifi_busy:
                    return
                self._dispatch_wifi_status(button)
            elif self.state == AppState.OVERLAY_WIFI_FORGET:
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_wifi_forget(r)
            elif self.state == AppState.OVERLAY_POWEROFF:
                # §7.8: OK confirms shutdown, BACK returns to the
                # screen that was visible when the chord fired.
                r = handle_overlay_button(button)
                if r is not None:
                    self._dispatch_overlay_poweroff(r)
            elif self.state == AppState.SHUTTING_DOWN:
                # §7.8 phase 1: the operator is no longer expected to
                # interact. Every button is inert until systemd halts
                # the service.
                return

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
            elif self.state == AppState.WIFI_LIST and self.wifi_list_ix is not None:
                # Revision 1: short OK/BACK fire on release so they are
                # distinguishable from the long-press edit/forget hooks.
                if self._wifi_busy:
                    return
                r = self.wifi_list_ix.on_release(button)
                if r is not None:
                    self._dispatch_wifi_list(r)

    def on_long_press(self, button) -> None:
        """Long-press hook. Consumed by the main screen and the Wi-Fi list."""
        with self.lock:
            if self.state == AppState.MAIN:
                r = self.main_ix.on_long_press(
                    button,
                    configs=tuple(self.configs),
                )
                if r is not None:
                    self._dispatch_main(r)
            elif self.state == AppState.WIFI_LIST and self.wifi_list_ix is not None:
                if self._wifi_busy:
                    return
                r = self.wifi_list_ix.on_long_press(button)
                if r is not None:
                    self._dispatch_wifi_list(r)

    def on_safe_shutdown_chord(self) -> None:
        """BACK+OK held 3 s — open the `Power off?` overlay (§7.8).

        Global hook: fires from any state. If we are already in the
        confirmation overlay or past it (`SHUTTING_DOWN`), the second
        chord is a no-op — the operator can release and re-attempt to
        cancel, but they cannot pile a second shutdown on top.
        """
        with self.lock:
            if self.state in (AppState.OVERLAY_POWEROFF, AppState.SHUTTING_DOWN):
                return
            self._prev_state_before_poweroff = self.state
            self.state = AppState.OVERLAY_POWEROFF

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
                warning = _past_date_warning(self.edit_ix.draft)
                return render_overlay(
                    self._render_edit_image(),
                    save_confirm(warning=warning),
                )
            if self.state == AppState.OVERLAY_DISCARD and self.edit_ix is not None:
                return render_overlay(self._render_edit_image(), discard_changes())
            if self.state == AppState.TIME_SETUP:
                return render_time_setup_menu(
                    main_img,
                    TimeSetupMenuState(
                        cursor=(
                            self.time_setup_ix.cursor
                            if self.time_setup_ix is not None else 0
                        ),
                        syncing_dots=self._compute_syncing_dots(),
                    ),
                )
            if self.state == AppState.PICKER and self.picker_ix is not None:
                # Pickers launched from edit sit over the edit screen;
                # the system-clock picker sits over the main screen.
                if self.picker_ix.target_field in ("start", "end") \
                        and self.edit_ix is not None:
                    base = self._render_edit_image()
                    title = f"Edit · {self.edit_ix.draft.name} · {self.picker_ix.target_field}"
                else:
                    base = main_img
                    title = "Set system clock"
                return render_datetime_picker(
                    base, self.picker_ix.state, title=title,
                )
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
            if self.state == AppState.OVERLAY_POWEROFF:
                # §7.8: chord can fire from any screen. Using main_img
                # as the underlay (always pre-computed above) gives a
                # consistent backdrop regardless of where the chord
                # came from — the operator's attention is on the
                # modal, not the dimmed background.
                return render_overlay(main_img, poweroff_confirm())
            if self.state == AppState.SHUTTING_DOWN:
                # §7.8: single combined message ("POWERING OFF…" +
                # LED hint). Painted from the moment OK is pressed
                # and persists through the kernel halt thanks to the
                # pitft22's internal frame memory.
                return render_powering_off()
            if self.state == AppState.WIFI_LIST and self.wifi_list_ix is not None:
                return render_wifi_list(
                    main_img,
                    WifiListState(
                        self.wifi_networks, self.wifi_list_ix.cursor,
                        scanning=self._wifi_busy,
                    ),
                    dots=self._wifi_dots(),
                )
            if self.state == AppState.WIFI_KEYBOARD and self.keyboard_ix is not None:
                title = (
                    "Network name" if self.keyboard_ix.target == "ssid"
                    else "Wi-Fi password"
                )
                return render_keyboard(main_img, self.keyboard_ix.state, title=title)
            if self.state == AppState.WIFI_STATUS and self.wifi_status_state is not None:
                return render_wifi_status(
                    main_img, self.wifi_status_state, dots=self._wifi_dots(),
                )
            if self.state == AppState.OVERLAY_WIFI_FORGET and self.wifi_list_ix is not None:
                wifi_img = render_wifi_list(
                    main_img,
                    WifiListState(
                        self.wifi_networks, self.wifi_list_ix.cursor,
                        scanning=False,
                    ),
                    dots=None,
                )
                return render_overlay(
                    wifi_img, wifi_forget_confirm(self._wifi_forget_target or "?"),
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
        # prd2.md §7: when a TrustedClock baseline exists, the visible
        # wall clock comes from `trusted_clock.now()` so the operator
        # sees exactly the time the schedule engine is firing on. With
        # no baseline (pre-first-sync) we fall back to `datetime.now()`
        # so first boot still renders something sensible.
        trusted_dt = (
            self.trusted_clock.now()
            if self.trusted_clock is not None and self.trusted_clock.has_baseline
            else None
        )
        wall_str = (trusted_dt or datetime.now()).strftime("%H:%M:%S")
        return self._main_screen.render(UIState(
            configs=tuple(self.configs),
            cursor=self.main_ix.cursor,
            engine_state=status.state,
            active_config_name=status.active_config_name,
            shots_taken=status.shots_taken,
            seconds_to_next_shot=status.seconds_to_next_shot,
            skips=status.skips,
            camera_connected=self.camera.is_connected(),
            wall_clock_str=wall_str,
            camera_not_responding=(
                status.consecutive_failures >= self._CAMERA_DOWN_THRESHOLD
            ),
            configs_reset=self._configs_reset,
            camera_model_label=self._camera_model_label(),
            dial_mismatch=self._camera_dial_mismatch(),
            schedule_state=self._compute_schedule_indicator(),
            schedule_disabled=not self.schedule_enabled,
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
        elif r.kind == MainAction.TOGGLE_SCHEDULE:
            self.toggle_schedule()
        elif r.kind == MainAction.OPEN_TIME_SETUP:
            self.time_setup_ix = TimeSetupMenuInteraction()
            self.main_ix.reset_input()
            self.state = AppState.TIME_SETUP

    def _dispatch_edit(self, action: EditAction) -> None:
        if action == EditAction.SAVE and self.edit_ix is not None:
            # OK in edit always asks for confirmation, regardless of
            # whether there are changes or it's a brand-new config —
            # saving is irreversible and the spec favours safety over
            # speed.
            self.state = AppState.OVERLAY_SAVE
            return
        if action in (EditAction.OPEN_PICKER_START, EditAction.OPEN_PICKER_END) \
                and self.edit_ix is not None:
            # prd2.md §6.2: OK on a START/END row opens the digit
            # picker in the field's current sub-mode.
            field = "start" if action == EditAction.OPEN_PICKER_START else "end"
            initial = getattr(self.edit_ix.draft, field)
            self.picker_ix = DateTimePickerInteraction(
                target_field=field,
                initial_value=initial,
            )
            self.state = AppState.PICKER
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

    def _dispatch_overlay_poweroff(self, ok: bool) -> None:
        """OK confirms the shutdown sequence; BACK cancels back to prev.

        On confirm:
            - state → `SHUTTING_DOWN` so the next render shows the
              `POWERING OFF…` screen with the LED hint, and every
              button becomes inert.
            - The injected shutdown action runs (Popens `/sbin/shutdown
              -h now`). It does NOT block — systemd starts firing
              SIGTERM within ~100 ms.

        The engine is intentionally NOT stopped here. The existing
        SIGTERM handler in `__main__.main()` reaches the loop's
        `finally:` block and tears down `scheduler` + `camera_health`
        + `camera` in the same sequence as a normal exit. Sync for an
        in-flight bracket is lost — identical to §5.3 behavior. The
        pitft22 panel retains the `POWERING OFF…` frame across the
        kernel halt until the operator pulls the powerbank.
        """
        if not ok:
            # BACK: restore the screen the operator was on. Engine
            # state untouched (a running timelapse keeps running).
            self.state = self._prev_state_before_poweroff or AppState.MAIN
            self._prev_state_before_poweroff = None
            return
        self.state = AppState.SHUTTING_DOWN
        try:
            self._shutdown_action()
        except Exception:
            # The action's own logging is enough; we already committed
            # to the `POWERING OFF…` screen and there's no useful UI
            # fallback. Operator can SSH in to diagnose.
            logger.exception("shutdown action raised")

    # -- Sync feedback (prd2.md addendum A1) ----------------------------

    # Hard timeout for the force-NTP-sync worker. If `timedatectl` or
    # the prober hang, the watchdog clears the syncing flag, closes the
    # menu and surfaces a WARNING in the log. 10 s is comfortably above
    # the typical 1–5 s subprocess duration without making the operator
    # wait too long on a true failure.
    _SYNC_WORKER_TIMEOUT_S: float = 10.0

    def _start_sync_worker(self) -> None:
        """Kick off the daemon worker that runs the NTP-sync sequence.

        Idempotent: a second call while a sync is already in flight
        is a no-op. Sets `_syncing` and records the start time BEFORE
        spawning so the next UI render already shows "Syncing.".
        """
        if self._syncing:
            return
        self._syncing = True
        self._syncing_started_mono = time.monotonic()
        self._notify_dirty()
        self._sync_worker_spawner(self._run_sync_worker)

    def _run_sync_worker(self) -> None:
        """Body of the force-NTP-sync worker (addendum A1).

        Runs OFF the UI thread. The sequence is: arm the trusted-clock
        force flag, run the OS-level sync request (blocking
        subprocess), then force an immediate prober poll so the new
        TimeUSec is observed before the next routine 60 s tick. A 10 s
        watchdog bounds the whole flow.

        On exit (success, failure, or timeout): clears `_syncing`,
        transitions back to MAIN if still in TIME_SETUP, sets
        `dirty_event`.
        """
        try:
            with self.lock:
                tc = self.trusted_clock
                pr = self.time_sync_prober
            if tc is not None:
                tc.force_trust_next_sync()
            if pr is not None:
                # Each step is best-effort; logged + swallowed on
                # failure. Watchdog covers the case where one of
                # these hangs.
                if not self._sync_timed_out():
                    try:
                        pr.request_force_sync()
                    except Exception:
                        logger.exception(
                            "sync worker: request_force_sync raised",
                        )
                if not self._sync_timed_out():
                    try:
                        pr.force_poll()
                    except Exception:
                        logger.exception(
                            "sync worker: force_poll raised",
                        )
            if self._sync_timed_out():
                logger.warning(
                    "sync worker: exceeded %.1f s budget — closing menu",
                    self._SYNC_WORKER_TIMEOUT_S,
                )
        except Exception:
            logger.exception("sync worker: unexpected exception")
        finally:
            with self.lock:
                self._syncing = False
                if self.state == AppState.TIME_SETUP:
                    self.state = AppState.MAIN
                    self.time_setup_ix = None
            self._notify_dirty()

    def _sync_timed_out(self) -> bool:
        return (
            time.monotonic() - self._syncing_started_mono
            > self._SYNC_WORKER_TIMEOUT_S
        )

    def _compute_syncing_dots(self) -> Optional[int]:
        """Animation phase for the TIME SETUP "Syncing..." label.

        Returns `None` when not syncing. Otherwise cycles `1 → 2 → 3
        → 1 → …` at ~2 Hz, driven by the wall-monotonic delta from the
        worker's start time. The UI loop's 250 ms idle timeout ensures
        the animation advances even when no other dirty events fire.
        """
        if not self._syncing:
            return None
        delta = time.monotonic() - self._syncing_started_mono
        # 2 Hz cycle, three phases.
        return int((delta * 2) % 3) + 1

    # -- Schedule dispatch & helpers (prd2.md §7) ------------------------

    def _dispatch_time_setup(self, action: TimeSetupMenuAction) -> None:
        if action == TimeSetupMenuAction.FORCE_NTP_SYNC:
            # Addendum A1: kick off a daemon worker so the UI thread
            # is never blocked on `timedatectl`. The menu stays open
            # and shows "Syncing..." until the worker completes (or
            # the 10 s watchdog fires); only then do we transition
            # back to MAIN.
            self._start_sync_worker()
            return
        if action == TimeSetupMenuAction.SET_MANUALLY:
            # Pre-populate the picker with the current best time
            # (trusted clock if available, else OS clock).
            if self.trusted_clock is not None:
                initial = self.trusted_clock.now() or datetime.now()
            else:
                initial = datetime.now()
            self.picker_ix = DateTimePickerInteraction(
                target_field="system_clock",
                initial_value=ScheduledMoment(
                    time=initial.time().replace(microsecond=0),
                    date=initial.date(),
                ),
            )
            self.state = AppState.PICKER
            self.time_setup_ix = None
            return
        if action == TimeSetupMenuAction.WIFI_SETUP:
            # Open the Wi-Fi list with a CACHED scan (fast, no --rescan).
            # Runs inline: the cached scan is quick; only the explicit
            # Rescan goes off-thread.
            try:
                networks = tuple(self._nmcli.scan())
            except Exception:
                logger.exception("wifi cached scan failed")
                networks = ()
            self.wifi_networks = networks
            self.wifi_list_ix = WifiListInteraction(networks)
            self.wifi_list_ix.reset_input()
            self.state = AppState.WIFI_LIST
            self.time_setup_ix = None
            return
        # CANCEL — no side effect.
        self.state = AppState.MAIN
        self.time_setup_ix = None

    def _dispatch_picker_save(self) -> None:
        """Picker OK with valid digits — route the moment.

        For `target_field in ("start", "end")` the moment lands on the
        edit draft via `dataclasses.replace`. Addendum F: if the picker
        was in NONE mode (chip = `[—]`), the moment is "cleared" —
        the field becomes `None`. For `"system_clock"` the moment is
        collapsed to a `datetime` and handed to `set_manual_time`.
        Both paths clear the picker and return to the screen that
        launched it.
        """
        assert self.picker_ix is not None
        field = self.picker_ix.target_field
        # Addendum F: NONE mode = "clear the field". The App reads
        # `is_clear_request` BEFORE calling `commit()` so the two
        # meanings of `commit() == None` (NONE vs. validation error)
        # stay disambiguated.
        if self.picker_ix.is_clear_request:
            if field in ("start", "end") and self.edit_ix is not None:
                self.edit_ix.draft = replace(
                    self.edit_ix.draft, **{field: None},
                )
            self.picker_ix = None
            self.state = AppState.EDIT
            return
        new_moment = self.picker_ix.commit()
        if new_moment is None:
            # commit() failed validation — `on_press` only got SAVE
            # because `_try_save` already returned SAVE; defensive
            # guard.
            return
        if field in ("start", "end") and self.edit_ix is not None:
            self.edit_ix.draft = replace(
                self.edit_ix.draft, **{field: new_moment},
            )
            self.picker_ix = None
            self.state = AppState.EDIT
        elif field == "system_clock":
            assert new_moment.date is not None  # forced for system_clock
            dt = datetime.combine(new_moment.date, new_moment.time)
            self.set_manual_time(dt)
            self.picker_ix = None
            self.state = AppState.MAIN
        else:
            # Unknown target — close defensively.
            self.picker_ix = None
            self.state = AppState.MAIN

    def _dispatch_picker_cancel(self) -> None:
        assert self.picker_ix is not None
        return_to = (
            AppState.MAIN if self.picker_ix.target_field == "system_clock"
            else AppState.EDIT
        )
        self.picker_ix = None
        self.state = return_to

    # -- Wi-Fi dispatch & worker (wifi-manual-config §5) -----------------

    def _dispatch_wifi_list(self, action: WifiListAction) -> None:
        assert self.wifi_list_ix is not None
        state = WifiListState(
            self.wifi_networks, self.wifi_list_ix.cursor, scanning=self._wifi_busy,
        )
        if action == WifiListAction.CONNECT:
            net = self.wifi_list_ix.selected_network(state)
            if net is None:
                return
            if net.secured and not net.saved:
                # Secured, no stored profile → ask for the password first.
                self._wifi_pending = (net.ssid, True, False)
                self.keyboard_ix = KeyboardInteraction(target="password")
                self.state = AppState.WIFI_KEYBOARD
            else:
                # Open OR saved-secured → connect immediately, no keyboard.
                # A saved-secured connect sends password=None so
                # NetworkManager reuses the stored secrets (Revision 1).
                self._wifi_pending = (net.ssid, net.secured, False)
                self._start_wifi_connect_worker()
            return
        if action == WifiListAction.EDIT:
            # Revision 1 — hold OK on a secured network: edit/replace the
            # password. Open the keyboard; the password Done path then
            # connects with the freshly typed password (creating/replacing
            # the profile).
            net = self.wifi_list_ix.selected_network(state)
            if net is None:
                return
            self._wifi_pending = (net.ssid, True, False)
            self.keyboard_ix = KeyboardInteraction(target="password")
            self.state = AppState.WIFI_KEYBOARD
            return
        if action == WifiListAction.OTHER:
            # Hidden / typed SSID — type the name first.
            self.keyboard_ix = KeyboardInteraction(target="ssid")
            self.state = AppState.WIFI_KEYBOARD
            return
        if action == WifiListAction.RESCAN:
            self._start_wifi_scan_worker()
            return
        if action == WifiListAction.FORGET:
            net = self.wifi_list_ix.selected_network(state)
            if net is None:
                return
            self._wifi_forget_target = net.ssid
            self.state = AppState.OVERLAY_WIFI_FORGET
            return
        # CANCEL — back to the SETTINGS menu (cursor on Wi-Fi setup).
        self.wifi_list_ix = None
        self.wifi_networks = ()
        self.time_setup_ix = TimeSetupMenuInteraction()
        self.time_setup_ix.cursor = 2   # the Wi-Fi setup item
        self.state = AppState.TIME_SETUP

    def _dispatch_keyboard_done(self) -> None:
        assert self.keyboard_ix is not None
        if self.keyboard_ix.target == "ssid":
            # `Other network…` SSID entered — always proceed to a
            # password keyboard (hidden secured network). Hidden *open*
            # networks are out of scope (the 8-char password minimum
            # makes a no-password hidden join unreachable).
            ssid = self.keyboard_ix.text
            self._wifi_pending = (ssid, True, True)
            self.keyboard_ix = KeyboardInteraction(target="password")
            return
        # Password entered → start the connect worker (it reads the
        # password from `keyboard_ix.text` before clearing it).
        assert self._wifi_pending is not None
        ssid, _secured, hidden = self._wifi_pending
        self._wifi_pending = (ssid, True, hidden)
        self._start_wifi_connect_worker()

    def _dispatch_keyboard_cancel(self) -> None:
        self.keyboard_ix = None
        # Revision 1 — clear the list's pressed flags so a held BACK that
        # cancelled the keyboard does not then fire FORGET on the list it
        # returns to (the `_back_pressed` guard race).
        if self.wifi_list_ix is not None:
            self.wifi_list_ix.reset_input()
        self.state = AppState.WIFI_LIST

    def _dispatch_wifi_status(self, button) -> None:
        # OK or BACK both return to the cached list (retry-friendly).
        if button in (ButtonId.OK, ButtonId.BACK):
            self.wifi_status_state = None
            if self.wifi_list_ix is not None:
                self.wifi_list_ix.reset_input()
            self.state = AppState.WIFI_LIST

    def _dispatch_wifi_forget(self, ok: bool) -> None:
        if ok and self._wifi_forget_target:
            try:
                self._nmcli.forget(self._wifi_forget_target)
            except Exception:
                logger.exception("wifi forget failed")
            # Refresh the cached list so the deleted profile disappears.
            try:
                self.wifi_networks = tuple(self._nmcli.scan())
            except Exception:
                logger.exception("wifi rescan after forget failed")
                self.wifi_networks = ()
            self.wifi_list_ix = WifiListInteraction(self.wifi_networks)
        self._wifi_forget_target = None
        if self.wifi_list_ix is not None:
            self.wifi_list_ix.reset_input()
        self.state = AppState.WIFI_LIST

    def _start_wifi_connect_worker(self) -> None:
        """Kick off the off-thread `nmcli connect`. Modelled on the NTP
        sync worker. The 30 s timeout lives inside `nmcli.connect`."""
        if self._wifi_busy:
            return
        assert self._wifi_pending is not None
        ssid, secured, hidden = self._wifi_pending
        pw = (
            self.keyboard_ix.text
            if secured and self.keyboard_ix is not None else None
        )
        self._wifi_busy = True
        self._wifi_busy_started_mono = time.monotonic()
        self.keyboard_ix = None
        self.wifi_status_state = WifiStatusState(phase="connecting", ssid=ssid)
        self.state = AppState.WIFI_STATUS
        self._notify_dirty()
        self._wifi_worker_spawner(
            lambda: self._run_wifi_connect(ssid, pw, hidden),
        )

    def _run_wifi_connect(self, ssid, pw, hidden) -> None:
        """Worker body — runs OFF the UI thread (subprocess outside the
        lock; state mutations re-acquire `self.lock`)."""
        from .net.nmcli import ConnectResult
        refreshed: Optional[tuple] = None
        try:
            result = self._nmcli.connect(ssid, pw, hidden=hidden)
            st = (
                self._nmcli.status()
                if result.outcome is ConnectOutcome.SUCCESS else None
            )
            if result.outcome is ConnectOutcome.SUCCESS:
                # Refresh the cached list so the active `●` dot (and the
                # `saved` flag) follow the new association — otherwise the
                # list keeps the stale active marker from the scan taken
                # on entry. Reconcile against the live connection name so
                # the dot is right regardless of any IN-USE lag in
                # `nmcli device wifi list`.
                try:
                    active_name = st.connection if st else ssid
                    refreshed = tuple(
                        replace(
                            n,
                            active=(n.ssid == active_name),
                            saved=(n.saved or n.ssid == active_name),
                        )
                        for n in self._nmcli.scan()
                    )
                except Exception:
                    logger.exception("post-connect list refresh failed")
        except Exception:
            logger.exception("wifi connect worker failed")
            result, st = ConnectResult(ConnectOutcome.FAILED, ssid), None
        finally:
            with self.lock:
                self._wifi_busy = False
                if refreshed is not None:
                    self.wifi_networks = refreshed
                    self.wifi_list_ix = WifiListInteraction(refreshed)
                    self.wifi_list_ix.reset_input()
                self.wifi_status_state = WifiStatusState(
                    phase=(
                        "connected" if result.outcome is ConnectOutcome.SUCCESS
                        else "failed"
                    ),
                    ssid=ssid,
                    ip=st.ip if st else None,
                    outcome=result.outcome,
                    detail=result.detail,
                )
                self.state = AppState.WIFI_STATUS
            self._notify_dirty()

    def _start_wifi_scan_worker(self) -> None:
        """Kick off the off-thread `nmcli scan --rescan yes`."""
        if self._wifi_busy:
            return
        self._wifi_busy = True
        self._wifi_busy_started_mono = time.monotonic()
        self._notify_dirty()
        self._wifi_worker_spawner(self._run_wifi_scan)

    def _run_wifi_scan(self) -> None:
        networks: tuple = ()
        try:
            networks = tuple(self._nmcli.scan(rescan=True))
        except Exception:
            logger.exception("wifi scan worker failed")
        finally:
            with self.lock:
                self._wifi_busy = False
                self.wifi_networks = networks
                self.wifi_list_ix = WifiListInteraction(networks)
                self.state = AppState.WIFI_LIST
            self._notify_dirty()

    def _wifi_dots(self) -> Optional[int]:
        """Animation phase for the Connecting…/Scanning… labels.

        Mirrors `_compute_syncing_dots`: `None` unless busy, else cycles
        `1 → 2 → 3` at ~2 Hz.
        """
        if not self._wifi_busy:
            return None
        delta = time.monotonic() - self._wifi_busy_started_mono
        return int((delta * 2) % 3) + 1

    # -- Schedule binding & providers ------------------------------------

    def bind_schedule(
        self,
        *,
        trusted_clock: TrustedClock,
        time_sync_prober: TimeSyncProber,
        schedule_evaluator: ScheduleEvaluator,
        schedule_store: ScheduleStateStore,
        initial_enabled: bool,
        dirty_event: Optional[threading.Event] = None,
    ) -> None:
        """Install the schedule trio on the App.

        Called once from `__main__.main()` after the scheduler and
        camera-health threads are running. Idempotent in the sense
        that calling it twice is harmless (replaces the references).
        """
        with self.lock:
            self.trusted_clock = trusted_clock
            self.time_sync_prober = time_sync_prober
            self.schedule_evaluator = schedule_evaluator
            self.schedule_store = schedule_store
            self.schedule_enabled = bool(initial_enabled)
            self._dirty_event = dirty_event

    def snapshot_configs(self) -> List[TimelapseConfig]:
        """Thread-safe copy of the current config list.

        Used as `configs_provider` for `ScheduleEvaluator`. The copy
        ensures the evaluator does not observe a half-mutated list
        while the App is in the middle of `_commit_edit` / `_delete`.
        """
        with self.lock:
            return list(self.configs)

    def is_schedule_enabled(self) -> bool:
        with self.lock:
            return self.schedule_enabled

    def active_config_name(self) -> Optional[str]:
        """Name of the engine's currently active config (or None).

        Used as `active_config_name_provider` for `ScheduleEvaluator`.
        """
        with self.lock:
            cfg = self.engine.active_config
            return cfg.name if cfg is not None else None

    def toggle_schedule(self) -> None:
        """Flip the persisted schedule_enabled flag.

        Mutates under `self.lock`. Persists synchronously (the store
        write is small). Sets the dirty event so the UI redraws and the
        evaluator picks up the new flag on the next tick.
        """
        with self.lock:
            self.schedule_enabled = not self.schedule_enabled
            if self.schedule_store is not None:
                try:
                    self.schedule_store.save(self.schedule_enabled)
                except Exception:
                    logger.exception("toggle_schedule: store.save raised")
            logger.info("schedule toggled to %s", self.schedule_enabled)
        self._notify_dirty()

    def force_trust_next_sync(self) -> None:
        """Arm the trusted-clock force flag, then nudge the prober.

        Order matters: setting the flag BEFORE the OS sync ensures any
        sync that lands while we are still in the middle of this method
        is interpreted as FORCED.
        """
        with self.lock:
            if self.trusted_clock is not None:
                self.trusted_clock.force_trust_next_sync()
            prober = self.time_sync_prober
        if prober is not None:
            prober.request_force_sync()
        self._notify_dirty()

    def on_sync_observed(self, wall_now: datetime) -> None:
        """Callback wired into `TimeSyncProber.on_sync`.

        Logs the outcome, sets the dirty event, and — crucially —
        calls `schedule_evaluator.reset_frontier()` on FIRST_SYNC or
        FORCED so the evaluator reseeds against the freshly-anchored
        baseline (see `implementation-notes.md` — this closes the
        deviation flagged during prd.md). ACCEPTED / REJECTED leave the
        frontier alone (the baseline either moved by a tiny envelope-
        sized amount or was rejected outright).
        """
        with self.lock:
            if self.trusted_clock is None:
                return
            outcome = self.trusted_clock.on_sync_observed(wall_now)
            logger.info(
                "on_sync_observed: outcome=%s wall=%s",
                outcome.value, wall_now.isoformat(),
            )
            if outcome in (SyncOutcome.FIRST_SYNC, SyncOutcome.FORCED) \
                    and self.schedule_evaluator is not None:
                self.schedule_evaluator.reset_frontier()
        self._notify_dirty()

    def set_manual_time(self, entered_dt: datetime) -> None:
        """Manual time entry commit path (from the Time Setup menu's
        "Set manually" option).

        Sets the OS clock via `timedatectl set-time` then anchors the
        trusted baseline as FORCED. Subprocess failures are logged at
        WARNING and the trusted clock is NOT touched (so a botched
        clock change does not silently re-anchor the baseline).
        """
        cmd_arg = entered_dt.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("set_manual_time: requesting timedatectl set-time '%s'",
                    cmd_arg)
        with self.lock:
            try:
                self._timedatectl_runner(["timedatectl", "set-time", cmd_arg])
            except Exception as e:
                logger.warning(
                    "set_manual_time: timedatectl failed (%s) — "
                    "OS clock and trusted clock unchanged", e,
                )
                return
            if self.trusted_clock is not None:
                self.trusted_clock.force_trust_next_sync()
                outcome = self.trusted_clock.on_sync_observed(entered_dt)
                logger.info(
                    "set_manual_time: trusted clock outcome=%s", outcome.value,
                )
                if outcome in (SyncOutcome.FIRST_SYNC, SyncOutcome.FORCED) \
                        and self.schedule_evaluator is not None:
                    self.schedule_evaluator.reset_frontier()
        self._notify_dirty()

    def _compute_schedule_indicator(self) -> ScheduleIndicator:
        """Map the trusted-clock state to a colored dot.

        Pure function of `trusted_clock` and `time_sync_prober`. The
        `schedule_enabled` flag is intentionally NOT consulted here:
        the colored dot reflects the *would-be* engine state, and the
        UI renders it alongside a strikethrough on the clock glyph
        when `schedule_enabled` is False — see `widgets.status_bar`
        and §6 of `prd2.md`. Called under `self.lock` by the renderer.
        """
        if self.trusted_clock is None or not self.trusted_clock.has_baseline:
            return ScheduleIndicator.RED
        if self.trusted_clock.is_glitched:
            return ScheduleIndicator.YELLOW
        last_mono = (
            self.time_sync_prober.last_successful_sync_at_monotonic()
            if self.time_sync_prober is not None else None
        )
        if last_mono is None \
                or (self._now_monotonic() - last_mono) > SCHEDULE_STALE_THRESHOLD_S:
            return ScheduleIndicator.YELLOW
        return ScheduleIndicator.GREEN

    def _notify_dirty(self) -> None:
        if self._dirty_event is not None:
            self._dirty_event.set()

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
