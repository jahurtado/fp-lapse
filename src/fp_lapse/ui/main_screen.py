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
from ..display.iface import new_canvas
from ..engine import EngineState
from . import theme, widgets

_FIRST_BLOCK_Y: int = 22  # just below the status-bar separator line
_NEW_ITEM_GAP: int = 2    # px of breathing room between the last config and "+ New ..."


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

        for i, cfg in enumerate(state.configs):
            cfg_running = (
                is_running and state.active_config_name == cfg.name
            )
            y = widgets.draw_config_block(
                draw, y, cfg,
                selected=(state.cursor == i),
                running=cfg_running,
                taken=state.shots_taken if cfg_running else None,
                next_in_s=state.seconds_to_next_shot if cfg_running else None,
            )

        widgets.draw_new_config_pseudo_item(
            draw,
            y + _NEW_ITEM_GAP,
            selected=(state.cursor == len(state.configs)),
        )

        widgets.footer(
            draw, footer_hint(state),
            version_stamp=f"v{__version__}",
        )
        return canvas


def footer_hint(state: UIState) -> str:
    """Footer hint string for the current engine state and cursor position.

    The footer is right-aligned (version stamp lives on the left), so
    hints use single-space separators between action groups instead of
    big internal padding. With the version label occupying the left
    ~46 px, hint widths must stay under ~260 px to keep a comfortable
    gap.
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
            return "↑↓ nav  OK new  BACK stop"
        if cursor_on_running:
            return "↑↓ nav  BACK stop"
        return "↑↓ nav  OK switch  BACK stop"
    if cursor_on_new:
        return "↑↓ nav  OK new"
    return "↑↓ nav  OK run  hold OK menu"


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
        return None  # LEFT/RIGHT reserved (§7.1)

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
