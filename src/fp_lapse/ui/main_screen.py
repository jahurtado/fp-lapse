"""Main screen of the intervalometer: config list (IDLE / RUNNING).

Two responsibilities live in this module, split into separate classes:

- `MainScreen` + `UIState`: pure rendering. Given a `UIState` it
  produces a 320x240 image without touching any hardware. Covers IDLE
  and RUNNING (the difference is which config has the `●` and what
  hint the footer shows — §7.1 / §7.2 of docs/reference.md).

- `MainScreenInteraction` + `MainAction(Result)`: cursor navigation
  and translation of buttons to high-level actions (start, switch,
  stop_confirm, open_manage, open_edit_new). Detects OK long-press
  (≥3 s) in `tick()` while the button is held.

The main loop combines both: it builds `UIState` from `ConfigStore`,
`EngineStatus` and the interaction's cursor; executes the actions the
interaction returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .. import __version__
from ..buttons.iface import ButtonId
from ..configs import TimelapseConfig
from ..display.iface import HEIGHT, new_canvas
from ..engine import EngineState
from . import theme, widgets
from .schedule_indicator import ScheduleIndicator

_FIRST_BLOCK_Y: int = 22  # just below the status-bar separator line
_NEW_ITEM_GAP: int = 2    # px of breathing room between the last config and "+ New ..."
_NEW_ITEM_HEIGHT: int = theme.ROW_HEIGHT + 2   # "+ New ..." text band height


def _compute_scroll_offset(
    cursor: int, heights: list, visible_h: int,
) -> int:
    """Addendum I: smallest "first visible block" index such that the
    cursor's block fits inside `visible_h`. Stateless — recomputed on
    every render from the cursor position and the per-block heights.

    `heights` has one entry per visible position: `len(configs)` config
    block heights followed by the `+ New ...` pseudo-row's height.
    `cursor` is `0..len(configs)` inclusive (last index = `+ New`).

    Returns the smallest `start` such that
    `sum(heights[start:cursor+1]) <= visible_h`. When the cursor's
    block alone exceeds `visible_h` (degenerate — very tall config),
    clamps to the cursor position so at least that block renders from
    the top of the visible area.

    Complexity: `O(n^2)` worst-case (the `sum(...)` inside the loop is
    `O(n)`). Intentional: the config list is hard-capped at 20 entries
    by `MAX_CONFIGS`, so the worst case is ~441 ops per render —
    invisible. A future refactor could precompute a cumulative-sum
    array and binary-search the first `start` whose tail sum fits
    (`O(n log n)`), but the simpler form is easier to follow at this
    scale.
    """
    start = 0
    while start <= cursor:
        if sum(heights[start:cursor + 1]) <= visible_h:
            return start
        start += 1
    return cursor


@dataclass(frozen=True)
class UIState:
    """Snapshot of everything the main screen needs.

    Composed in the main loop from `ConfigStore`, `EngineStatus`,
    camera state, and wall-clock time. Keeping the render pure
    relative to `UIState` makes visual tests easy (same inputs → same
    pixels).
    """

    configs: Tuple[TimelapseConfig, ...]
    cursor: int                       # 0..len(configs)-1, or len(configs) for "+ New"
    engine_state: EngineState
    active_config_name: Optional[str] # name of the running config
    shots_taken: int                  # ignored if not running
    seconds_to_next_shot: Optional[float]
    skips: int
    camera_connected: bool
    wall_clock_str: str               # "HH:MM:SS"
    # Persistent banners (§6.1, §6.3). Set by the App from
    # `engine.consecutive_failures` and `app._configs_reset`.
    camera_not_responding: bool = False
    configs_reset: bool = False
    # Live camera identity for the status bar. "fp" (Sigma) by default;
    # "D5600" when the Nikon is detected. Updates on hot-swap.
    camera_model_label: str = "fp"
    # D5600 mode-dial mismatch: the engine wants MANUAL/PROGRAM but the
    # physical dial disagrees. Shows a "DIAL NOT ON M" warning.
    dial_mismatch: bool = False
    # Schedule status (prd2.md §6): drives the 4-state colored-dot in
    # the status bar. Default OFF preserves the legacy mockups.
    schedule_state: ScheduleIndicator = ScheduleIndicator.OFF
    # Schedule enable flag (§6 addendum): when True the clock glyph is
    # rendered with a diagonal strikethrough — the dot still reflects
    # the would-be engine state. Default False keeps the legacy "no
    # strikethrough" look for screens constructed without this info.
    schedule_disabled: bool = False


class MainScreen:
    """Renders the main screen from a `UIState`."""

    def render(self, state: UIState) -> Image.Image:
        canvas = new_canvas(theme.BG)
        draw = ImageDraw.Draw(canvas)
        is_running = state.engine_state == EngineState.RUNNING

        widgets.status_bar(
            draw,
            time_str=state.wall_clock_str,
            cam_connected=state.camera_connected,
            skips=state.skips,
            show_skips=is_running or state.skips > 0,
            model_label=state.camera_model_label,
            dial_mismatch=state.dial_mismatch,
            schedule_state=state.schedule_state,
            schedule_disabled=state.schedule_disabled,
            version_stamp=f"v{__version__}",
        )

        # Persistent banners (§6.1, §6.3). When both fire the camera
        # alert (red, more urgent) goes on top.
        y = _FIRST_BLOCK_Y
        if state.camera_not_responding:
            y = widgets.draw_banner(draw, y, "CAMERA NOT RESPONDING",
                                    severity="error")
        if state.configs_reset:
            y = widgets.draw_banner(draw, y, "CONFIGS RESET",
                                    severity="warn")

        # Addendum I: auto-scroll the config list so the cursor's block
        # is fully visible. Heights are computed up front (one entry per
        # config + one for the `+ New ...` virtual row).
        visible_y_bottom = HEIGHT - theme.FOOTER_HEIGHT
        visible_h = visible_y_bottom - y
        block_heights = []
        for cfg in state.configs:
            cfg_running = is_running and state.active_config_name == cfg.name
            block_heights.append(
                widgets.config_block_height(
                    len(cfg.shots),
                    running=cfg_running,
                    is_auto=cfg.is_auto,
                    schedule_lines=len(widgets.format_schedule_lines(cfg)),
                )
            )
        block_heights.append(_NEW_ITEM_GAP + _NEW_ITEM_HEIGHT)
        scroll_offset = _compute_scroll_offset(
            cursor=state.cursor,
            heights=block_heights,
            visible_h=visible_h,
        )

        for i in range(scroll_offset, len(state.configs)):
            next_y = y + block_heights[i]
            # Stop before drawing a block that would overflow — unless
            # it's the cursor's block, in which case we always show it
            # (the auto-scroll guarantees the cursor is the last block
            # to land inside the visible area).
            if next_y > visible_y_bottom and i != state.cursor:
                break
            cfg = state.configs[i]
            cfg_running = is_running and state.active_config_name == cfg.name
            y = widgets.draw_config_block(
                draw, y, cfg,
                selected=(state.cursor == i),
                running=cfg_running,
                taken=state.shots_taken if cfg_running else None,
                next_in_s=state.seconds_to_next_shot if cfg_running else None,
            )

        # `+ New ...` only renders when there is room left below the
        # last drawn block. The auto-scroll guarantees the row is
        # visible whenever the cursor lands on it.
        new_y = y + _NEW_ITEM_GAP
        if new_y + _NEW_ITEM_HEIGHT <= visible_y_bottom:
            widgets.draw_new_config_pseudo_item(
                draw,
                new_y,
                selected=(state.cursor == len(state.configs)),
            )

        primary, secondary = footer_hint(state)
        widgets.footer(draw, primary, hint2=secondary)
        return canvas


# Secondary footer line — same for every main-screen state. Carries
# the three global shortcuts that aren't state-dependent:
#   LEFT       → opens the SETTINGS modal menu
#   RIGHT      → toggles the persisted `schedule_enabled` flag
#   BACK+OK    → safe shutdown chord (§7.8), 3 s hold
# Width budget at mono-11: ~42 chars × ~7 px = ~294 px (fits within
# the ~312 px usable area with comfortable margin).
_SECONDARY_HINT: str = "← settings  → sched on/off  OK+ESC shutdown"


def footer_hint(state: UIState) -> tuple[str, str]:
    """Returns `(primary, secondary)` footer lines for the current state.

    The primary line carries the state-dependent actions the operator
    will press most often (OK / BACK / hold OK). The secondary line is
    constant: the three global shortcuts (LEFT / RIGHT / BACK+OK
    chord) — see `_SECONDARY_HINT`.

    Primary mono-11 width budget: the version stamp lives in the
    status bar (top-right) now, so the footer right edge is free —
    hints can use the full ~310 px before clipping.

    Earlier design (single-line footer) inlined `← time → sched`
    selectively per state when room allowed; that hid the global
    shortcuts in the most common IDLE-on-real-config state. The
    two-line footer (FOOTER_HEIGHT bumped 16→28) buys back the
    discoverability without sacrificing the `hold OK menu` primary
    hint.
    """
    cursor_on_new = state.cursor == len(state.configs)
    cursor_on_running = (
        not cursor_on_new
        and state.engine_state == EngineState.RUNNING
        and 0 <= state.cursor < len(state.configs)
        and state.configs[state.cursor].name == state.active_config_name
    )
    if state.engine_state == EngineState.RUNNING:
        if cursor_on_new:
            primary = "↑↓ nav  OK new  ESC stop"
        elif cursor_on_running:
            primary = "↑↓ nav  ESC stop"
        else:
            primary = "↑↓ nav  OK switch  ESC stop"
    elif cursor_on_new:
        primary = "↑↓ nav  OK new"
    else:
        # IDLE on real config: `hold OK menu` is the discoverability
        # path into the manage menu (addendum C).
        primary = "↑↓ nav  OK run  hold OK menu"
    return primary, _SECONDARY_HINT


# ----------------------------------------------------------------------
# Interaction: buttons → high-level actions
# ----------------------------------------------------------------------
# The 3 s long-press threshold lives in `__main__.LONG_PRESS_S` — the
# `threading.Timer` there decides when to call `on_long_press()`. The
# interaction itself is timing-agnostic.


class MainAction(str, Enum):
    """Actions the main screen can ask the main loop to perform."""

    START = "start"                    # start the config under cursor
    SWITCH = "switch"                  # switch to the config under cursor (hot)
    STOP_CONFIRM = "stop_confirm"      # open the stop overlay (BACK in RUNNING)
    OPEN_MANAGE = "open_manage"        # open the manage menu (OK long on a config)
    OPEN_EDIT_NEW = "open_edit_new"    # create a new config (OK on + New)
    # prd2.md §6.1 — schedule wiring of LEFT/RIGHT on the main screen.
    TOGGLE_SCHEDULE = "toggle_schedule"     # RIGHT: flip the persisted schedule_enabled flag
    OPEN_TIME_SETUP = "open_time_setup"     # LEFT: open the TIME SETUP modal menu


@dataclass(frozen=True)
class MainActionResult:
    kind: MainAction
    cfg: Optional[TimelapseConfig] = None


class MainScreenInteraction:
    """Cursor + button-to-action translation for the main screen.

    Long-press detection is **external**: the main loop (or whatever
    drives this interaction) arms a `threading.Timer` on each OK press
    and calls `on_long_press()` if the timer fires before release. The
    interaction tracks `_ok_pressed` and `_ok_long_fired` so the
    release path can distinguish short presses from long ones.

    Minimal internal state:
      - `cursor` (0..N where N == len(configs) represents "+ New").
      - `_ok_pressed`: True between OK press and release.
      - `_ok_long_fired`: set by `on_long_press()` so the trailing
        release doesn't also fire a short action.
    """

    def __init__(self) -> None:
        self.cursor: int = 0
        self._ok_pressed: bool = False
        self._ok_long_fired: bool = False

    def on_press(
        self,
        button: ButtonId,
        *,
        configs: Sequence[TimelapseConfig],
        engine_state: EngineState,
    ) -> Optional[MainActionResult]:
        slots = len(configs) + 1  # configs + "+ New"
        if button == ButtonId.UP:
            self.cursor = max(0, self.cursor - 1)
            return None
        if button == ButtonId.DOWN:
            self.cursor = min(slots - 1, self.cursor + 1)
            return None
        if button == ButtonId.OK:
            self._ok_pressed = True
            self._ok_long_fired = False
            return None
        if button == ButtonId.BACK:
            if engine_state == EngineState.RUNNING:
                return MainActionResult(MainAction.STOP_CONFIRM)
            return None  # §7.1: BACK in IDLE = no-op
        # prd2.md §6.1 — schedule wiring (LEFT/RIGHT no longer reserved).
        if button == ButtonId.LEFT:
            return MainActionResult(MainAction.OPEN_TIME_SETUP)
        if button == ButtonId.RIGHT:
            return MainActionResult(MainAction.TOGGLE_SCHEDULE)
        return None

    def on_release(
        self,
        button: ButtonId,
        *,
        configs: Sequence[TimelapseConfig],
        engine_state: EngineState,
        active_config_name: Optional[str] = None,
    ) -> Optional[MainActionResult]:
        if button != ButtonId.OK or not self._ok_pressed:
            return None
        self._ok_pressed = False
        if self._ok_long_fired:
            # Long press already fired; the release is a no-op.
            return None
        return self._short_press_action(configs, engine_state, active_config_name)

    def on_long_press(
        self,
        button: ButtonId,
        *,
        configs: Sequence[TimelapseConfig],
    ) -> Optional[MainActionResult]:
        """Long-press hook. Called by the external timer at the 3 s mark.

        If the user has already released OK, `_ok_pressed` is False and
        we skip (the timer should have been cancelled, but a race
        between release and the timer's own fire is possible — guard
        against firing a stale long-press).
        """
        if button != ButtonId.OK or not self._ok_pressed:
            return None
        self._ok_long_fired = True
        return self._long_press_action(configs)

    def reset_input(self) -> None:
        """Clear input state. Call when losing focus (manage menu /
        overlay) and when regaining it, so a stale OK from before
        doesn't trigger stale actions."""
        self._ok_pressed = False
        self._ok_long_fired = False

    def _short_press_action(
        self,
        configs: Sequence[TimelapseConfig],
        engine_state: EngineState,
        active_config_name: Optional[str],
    ) -> Optional[MainActionResult]:
        if self.cursor >= len(configs):
            return MainActionResult(MainAction.OPEN_EDIT_NEW)
        cfg = configs[self.cursor]
        if engine_state == EngineState.RUNNING:
            if active_config_name == cfg.name:
                return None  # §7.2: OK on the running config = no effect
            return MainActionResult(MainAction.SWITCH, cfg=cfg)
        return MainActionResult(MainAction.START, cfg=cfg)

    def _long_press_action(
        self,
        configs: Sequence[TimelapseConfig],
    ) -> Optional[MainActionResult]:
        if self.cursor >= len(configs):
            return None  # §7.1: no manage menu over "+ New"
        return MainActionResult(MainAction.OPEN_MANAGE, cfg=configs[self.cursor])
