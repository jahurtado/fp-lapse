"""SETTINGS menu modal (prd2.md §6 + wifi-manual-config §1).

Opened by short-pressing LEFT on the main screen. Flat, single-level —
three items:

    Sync Time (NTP) · Set Time (Manual) · Wi-Fi setup

The first two are the original TIME SETUP items, relabelled — their
dispatch behaviour (`FORCE_NTP_SYNC` / `SET_MANUALLY`) is unchanged. The
third (`WIFI_SETUP`) opens the on-device Wi-Fi setup flow. Module and
symbol names keep the historical `TimeSetup*` spelling for a legible
diff (the optional rename in the PRD was not taken).

Mirrors the structural conventions of `manage_menu.py` (shaded base,
centered dialog, header, selection band). BACK closes the menu without
any side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from PIL import Image, ImageDraw

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme, widgets

_BODY_PT: int = 11

MENU_ITEMS: Tuple[str, ...] = (
    "Sync Time (NTP)", "Set Time (Manual)", "Wi-Fi setup",
)

# Box geometry — sized to wrap the three items + title comfortably.
_BOX_W: int = 200
_BOX_H: int = 96
_BOX_X: int = (WIDTH - _BOX_W) // 2
_BOX_Y: int = (HEIGHT - _BOX_H) // 2

_HEADER_X_PAD: int = 12
_HEADER_Y: int = 10
_SEPARATOR_Y: int = 28
_ITEMS_TOP_Y: int = 38
_ITEM_STRIDE: int = 18

_BAND_LEFT_INSET: int = 4
_BAND_RIGHT_INSET: int = 4
_BAR_INNER_X: int = 7   # yellow bar width measured from _BOX_X + 4


@dataclass(frozen=True)
class TimeSetupMenuState:
    """Render state: which item is under the cursor.

    `syncing_dots` (addendum A1) controls the in-progress feedback for
    the Force NTP sync action. `None` means the menu is idle (existing
    behaviour). When set to `1`, `2`, or `3`, the first menu item
    renders as `Syncing` followed by that many `.`, with the cursor
    highlight locked there — the App caller drives the animation by
    cycling the value (typically via `time.monotonic()` modulo 3).
    """

    cursor: int                  # 0..len(MENU_ITEMS)-1
    syncing_dots: Optional[int] = None


def render_time_setup_menu(
    base: Image.Image, state: TimeSetupMenuState,
) -> Image.Image:
    """Compose the TIME SETUP menu over `base`. Returns fresh RGB 320x240."""
    if not (0 <= state.cursor < len(MENU_ITEMS)):
        raise ValueError(
            f"cursor must be in [0, {len(MENU_ITEMS)}), got {state.cursor}"
        )
    if state.syncing_dots is not None and not (1 <= state.syncing_dots <= 3):
        raise ValueError(
            f"syncing_dots must be None or 1..3, got {state.syncing_dots}"
        )

    # Addendum G: opaque screen transition (see picker_datetime.py).
    rgba, draw = widgets.new_overlay_canvas(base)
    font = fonts.mono(_BODY_PT)

    # Dialog box
    draw.rectangle(
        [_BOX_X, _BOX_Y, _BOX_X + _BOX_W, _BOX_Y + _BOX_H],
        fill=theme.DIALOG_BG,
        outline=theme.DIALOG_BORDER,
    )

    # Title + separator
    draw.text(
        (_BOX_X + _HEADER_X_PAD, _BOX_Y + _HEADER_Y),
        "SETTINGS", font=font, fill=theme.FG,
    )
    draw.line(
        [
            (_BOX_X + _HEADER_X_PAD, _BOX_Y + _SEPARATOR_Y),
            (_BOX_X + _BOX_W - _HEADER_X_PAD, _BOX_Y + _SEPARATOR_Y),
        ],
        fill=theme.SEP,
    )

    # When syncing, the cursor highlight is locked on item 0 and that
    # item renders the animated `Syncing<dots>` (addendum A1).
    is_syncing = state.syncing_dots is not None
    effective_cursor = 0 if is_syncing else state.cursor

    y = _BOX_Y + _ITEMS_TOP_Y
    for i, item in enumerate(MENU_ITEMS):
        if i == 0 and is_syncing:
            assert state.syncing_dots is not None  # narrowing for type
            label = "Syncing" + ("." * state.syncing_dots)
        else:
            label = item
        if i == effective_cursor:
            draw.rectangle(
                [
                    _BOX_X + _BAND_LEFT_INSET, y - 1,
                    _BOX_X + _BOX_W - _BAND_RIGHT_INSET, y + 13,
                ],
                fill=theme.SEL_BG,
            )
            draw.rectangle(
                [_BOX_X + _BAND_LEFT_INSET, y - 1,
                 _BOX_X + _BAR_INNER_X, y + 13],
                fill=theme.SEL_BAR,
            )
            text_color = theme.SEL_FG
        else:
            text_color = theme.FG
        draw.text(
            (_BOX_X + _HEADER_X_PAD, y),
            label, font=font, fill=text_color,
        )
        y += _ITEM_STRIDE

    return rgba.convert("RGB")


# ----------------------------------------------------------------------
# Interaction
# ----------------------------------------------------------------------


class TimeSetupMenuAction(str, Enum):
    """Action selected from the SETTINGS menu (prd2.md §6 + wifi §1)."""

    FORCE_NTP_SYNC = "force_ntp_sync"
    SET_MANUALLY = "set_manually"
    WIFI_SETUP = "wifi_setup"
    CANCEL = "cancel"


_ACTION_BY_INDEX: Tuple[TimeSetupMenuAction, ...] = (
    TimeSetupMenuAction.FORCE_NTP_SYNC,
    TimeSetupMenuAction.SET_MANUALLY,
    TimeSetupMenuAction.WIFI_SETUP,
)
assert len(_ACTION_BY_INDEX) == len(MENU_ITEMS)


class TimeSetupMenuInteraction:
    """Cursor navigation + button-to-action translation.

    BACK is equivalent to Cancel (returns `TimeSetupMenuAction.CANCEL`).
    LEFT/RIGHT do nothing inside the menu.
    """

    def __init__(self) -> None:
        self.cursor: int = 0

    def on_press(self, button: ButtonId) -> Optional[TimeSetupMenuAction]:
        if button == ButtonId.UP:
            self.cursor = max(0, self.cursor - 1)
            return None
        if button == ButtonId.DOWN:
            self.cursor = min(len(MENU_ITEMS) - 1, self.cursor + 1)
            return None
        if button == ButtonId.OK:
            return _ACTION_BY_INDEX[self.cursor]
        if button == ButtonId.BACK:
            return TimeSetupMenuAction.CANCEL
        return None  # LEFT/RIGHT don't apply

    def reset(self) -> None:
        """Return the cursor to the first item (Force NTP sync)."""
        self.cursor = 0
