"""Modal Yes/No confirmation overlays (§7.4 of docs/reference.md).

Four variants triggered by the rest of the app from different places:

- `stop_confirm()`         — BACK with the engine RUNNING (§5.3).
- `save_confirm()`         — OK in edit (with or without changes).
- `discard_changes()`      — BACK in edit with pending changes.
- `delete_confirm(name)`   — Manage menu → Delete (§7.5).

All share the same layout: translucent shade over the active screen,
a centred 240x90 box with title, optional dimmed body below, and the
`OK yes        BACK no` hint at the bottom. The caller passes the base
image of the screen that was behind + the `OverlayDialog`; the
overlay doesn't know anything about that base.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme, widgets

_BODY_PT: int = 11

_DIALOG_W: int = 240
_DIALOG_H: int = 90
_DIALOG_X: int = (WIDTH - _DIALOG_W) // 2
_DIALOG_Y: int = (HEIGHT - _DIALOG_H) // 2

# y values relative to the top of the dialog box.
_TITLE_Y: int = 18
_BODY_Y: int = 36
_HINT_Y: int = 62


@dataclass(frozen=True)
class OverlayDialog:
    """Modal dialog content — not coupled to its context."""

    title: str
    body: Optional[str] = None
    hint: str = "OK yes        BACK no"


def stop_confirm() -> OverlayDialog:
    """BACK during RUNNING (§5.3): confirms the engine stop."""
    return OverlayDialog(title="Stop the timelapse?", body="Sync will be lost.")


def save_confirm() -> OverlayDialog:
    """OK in edit: confirms the save."""
    return OverlayDialog(title="Save changes?")


def discard_changes() -> OverlayDialog:
    """BACK in edit with pending changes."""
    return OverlayDialog(title="Discard changes?")


def delete_confirm(config_name: str) -> OverlayDialog:
    """Manage menu → Delete: confirms the config removal."""
    return OverlayDialog(title=f"Delete '{config_name}'?")


def render_overlay(base: Image.Image, dialog: OverlayDialog) -> Image.Image:
    """Compose `dialog` on top of `base`. Returns a fresh RGB 320x240 image."""
    if base.size != (WIDTH, HEIGHT):
        raise ValueError(
            f"base must be {WIDTH}x{HEIGHT}, got {base.size}"
        )
    rgba = base.convert("RGBA")
    shade = Image.new("RGBA", (WIDTH, HEIGHT), theme.OVERLAY_SHADE)
    rgba.alpha_composite(shade)

    draw = ImageDraw.Draw(rgba)
    font = fonts.mono(_BODY_PT)

    draw.rectangle(
        [_DIALOG_X, _DIALOG_Y, _DIALOG_X + _DIALOG_W, _DIALOG_Y + _DIALOG_H],
        fill=theme.DIALOG_BG,
        outline=theme.DIALOG_BORDER,
    )

    _draw_centered(draw, dialog.title, _DIALOG_Y + _TITLE_Y, font, theme.FG)
    if dialog.body:
        _draw_centered(draw, dialog.body, _DIALOG_Y + _BODY_Y, font, theme.DIM)
    _draw_centered(draw, dialog.hint, _DIALOG_Y + _HINT_Y, font, theme.FG)

    return rgba.convert("RGB")


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    color,
) -> None:
    tw = widgets.text_width(draw, text, font)
    draw.text(
        (_DIALOG_X + (_DIALOG_W - tw) // 2, y),
        text, font=font, fill=color,
    )


def handle_overlay_button(button: ButtonId) -> Optional[bool]:
    """Translate a button event on a Yes/No overlay (§7.4).

    Returns:
      - `True`  if the user confirms (OK)
      - `False` if they cancel (BACK)
      - `None`  if the button does not apply (↑↓ ← →)
    """
    if button == ButtonId.OK:
        return True
    if button == ButtonId.BACK:
        return False
    return None
