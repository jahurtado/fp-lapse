"""Manage menu modal (§7.5 of docs/reference.md).

Submenu that opens with OK held ≥3 s on an existing configuration on
the main screen. Four fixed actions:

    Edit · Duplicate · Delete · Cancel

Does not depend on the engine state (it can open in IDLE or RUNNING);
it does NOT open over `+ New configuration` (§7.1).

Same architecture as `overlays.py`: the module exposes a pure render
that takes the `base` image (the screen that was behind) and a
`ManageMenuState`, and returns a fresh 320x240 RGB image.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from PIL import Image, ImageDraw

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme

_BODY_PT: int = 11

MENU_ITEMS: Tuple[str, ...] = ("Edit", "Duplicate", "Delete", "Cancel")

# Box geometry.
_BOX_W: int = 200
_BOX_H: int = 130
_BOX_X: int = (WIDTH - _BOX_W) // 2
_BOX_Y: int = (HEIGHT - _BOX_H) // 2

_HEADER_X_PAD: int = 12
_HEADER_Y: int = 10
_SEPARATOR_Y: int = 28
_ITEMS_TOP_Y: int = 38
_ITEM_STRIDE: int = 18

# Selection band inside the box (narrower than the box width).
_BAND_LEFT_INSET: int = 4
_BAND_RIGHT_INSET: int = 4
_BAR_INNER_X: int = 7   # yellow bar width measured from _BOX_X + 4


@dataclass(frozen=True)
class ManageMenuState:
    """Manage menu state: which config, which item is under the cursor."""

    config_name: str
    cursor: int                      # 0..len(MENU_ITEMS)-1


def render_manage_menu(
    base: Image.Image, state: ManageMenuState,
) -> Image.Image:
    """Compose the manage menu over `base`. Returns a fresh RGB 320x240 image."""
    if base.size != (WIDTH, HEIGHT):
        raise ValueError(
            f"base must be {WIDTH}x{HEIGHT}, got {base.size}"
        )
    if not (0 <= state.cursor < len(MENU_ITEMS)):
        raise ValueError(
            f"cursor must be in [0, {len(MENU_ITEMS)}), got {state.cursor}"
        )

    rgba = base.convert("RGBA")
    shade = Image.new("RGBA", (WIDTH, HEIGHT), theme.OVERLAY_SHADE)
    rgba.alpha_composite(shade)

    draw = ImageDraw.Draw(rgba)
    font = fonts.mono(_BODY_PT)

    # Box
    draw.rectangle(
        [_BOX_X, _BOX_Y, _BOX_X + _BOX_W, _BOX_Y + _BOX_H],
        fill=theme.DIALOG_BG,
        outline=theme.DIALOG_BORDER,
    )

    # Header: config name + separator line.
    draw.text(
        (_BOX_X + _HEADER_X_PAD, _BOX_Y + _HEADER_Y),
        state.config_name, font=font, fill=theme.FG,
    )
    draw.line(
        [
            (_BOX_X + _HEADER_X_PAD, _BOX_Y + _SEPARATOR_Y),
            (_BOX_X + _BOX_W - _HEADER_X_PAD, _BOX_Y + _SEPARATOR_Y),
        ],
        fill=theme.SEP,
    )

    # Items
    y = _BOX_Y + _ITEMS_TOP_Y
    for i, item in enumerate(MENU_ITEMS):
        if i == state.cursor:
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
            item, font=font, fill=text_color,
        )
        y += _ITEM_STRIDE

    return rgba.convert("RGB")


# ----------------------------------------------------------------------
# Manage menu interaction
# ----------------------------------------------------------------------


class ManageMenuAction(str, Enum):
    """Action selected from the manage menu (§7.5)."""

    EDIT = "edit"
    DUPLICATE = "duplicate"
    DELETE = "delete"
    CANCEL = "cancel"


# Positional mapping: the order in MENU_ITEMS determines which action
# each index corresponds to.
_ACTION_BY_INDEX: Tuple[ManageMenuAction, ...] = (
    ManageMenuAction.EDIT,
    ManageMenuAction.DUPLICATE,
    ManageMenuAction.DELETE,
    ManageMenuAction.CANCEL,
)
assert len(_ACTION_BY_INDEX) == len(MENU_ITEMS)


class ManageMenuInteraction:
    """Manage menu cursor + button-to-action translation.

    BACK is equivalent to Cancel (§7.5: "BACK closes the menu without
    action").
    """

    def __init__(self) -> None:
        self.cursor: int = 0

    def on_press(self, button: ButtonId) -> Optional[ManageMenuAction]:
        if button == ButtonId.UP:
            self.cursor = max(0, self.cursor - 1)
            return None
        if button == ButtonId.DOWN:
            self.cursor = min(len(MENU_ITEMS) - 1, self.cursor + 1)
            return None
        if button == ButtonId.OK:
            return _ACTION_BY_INDEX[self.cursor]
        if button == ButtonId.BACK:
            return ManageMenuAction.CANCEL
        return None  # LEFT/RIGHT don't apply in the menu

    def reset(self) -> None:
        """Return the cursor to the first item (Edit)."""
        self.cursor = 0
