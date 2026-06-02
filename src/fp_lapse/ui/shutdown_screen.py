"""Shutdown screen (§7.8 of docs/reference.md).

One terminal frame painted as soon as the operator confirms `Power off?`.
It combines the in-progress signal (`POWERING OFF…`) with the next
operator action (`unplug when the green LED is off`) in a single
honest message that persists for the entire shutdown sequence —
including after the kernel halts: the pitft22 panel retains the last
frame in its own memory until 3.3 V is cut at the GPIO header.

A two-phase design (`SHUTTING DOWN…` then `SAFE TO DISCONNECT`) was
considered but rejected: the first phase was visible for ~200 ms in
practice (systemd starts SIGTERM-ing the service within a few
hundred ms of `/sbin/shutdown -h now`), too brief to read, so the
operator only ever effectively saw the second phase. A single
always-correct message removes the timing race entirely.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme


_TITLE_PT: int = 22
_HINT_PT: int = 10


def _new_canvas() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), theme.BG)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: object,
    y: int,
    color: tuple,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)  # type: ignore[arg-type]
    w = bbox[2] - bbox[0]
    x = (WIDTH - w) // 2
    # textbbox's top is usually negative for ascender-dominated fonts —
    # subtract it so `y` lands at the visual top of the glyph block.
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=color)  # type: ignore[arg-type]


def render_powering_off() -> Image.Image:
    """Single shutdown frame — visible from the moment OK is pressed
    until the operator unplugs the powerbank.

    Two lines: a green title that doubles as the "we're shutting down"
    signal and a dim hint underneath telling the operator what signal
    to wait for before disconnecting.
    """
    img = _new_canvas()
    draw = ImageDraw.Draw(img)
    title_font = fonts.proportional(_TITLE_PT)
    hint_font = fonts.proportional(_HINT_PT)
    _draw_centered(draw, "POWERING OFF…", font=title_font, y=96, color=theme.OK_DOT)
    _draw_centered(
        draw,
        "Unplug the powerbank when the green LED is off.",
        font=hint_font, y=140, color=theme.DIM,
    )
    return img
