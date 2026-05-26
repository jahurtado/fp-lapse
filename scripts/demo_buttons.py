#!/usr/bin/env python3
"""demo_buttons.py — combined Mac mock demo (display + buttons).

Opens a Tk window with the 6-button grid (same layout as the real
test_buttons.py on the Pi) and responds to:

    ←  ↑  ↓  →     direction buttons
    Enter          OK
    Esc            BACK

Each cell lights up in orange while its mapped key is held down. Hold OK
plus BACK together for 1 second to quit (mirrors the real Pi behaviour).

This validates that TkDisplay + TkButtonPanel work together over the
shared Tk root and that the buttons API matches the same Protocol the
real GpioButtonPanel does.

Run:
    uv run python scripts/demo_buttons.py
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from fp_lapse.buttons import ButtonId  # noqa: E402
from fp_lapse.buttons.mock import TkButtonPanel  # noqa: E402
from fp_lapse.display import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.display.mock import TkDisplay  # noqa: E402


REFRESH_HZ = 30
EXIT_HOLD_S = 1.0

LAYOUT: list[list[ButtonId]] = [
    [ButtonId.LEFT, ButtonId.UP],
    [ButtonId.RIGHT, ButtonId.DOWN],
    [ButtonId.BACK, ButtonId.OK],
]

# Keyboard label shown on each cell (replaces the GPIO BCM number that the
# Pi diagnostic shows).
KEY_LABEL: dict[ButtonId, str] = {
    ButtonId.UP: "Up",
    ButtonId.DOWN: "Down",
    ButtonId.LEFT: "Left",
    ButtonId.RIGHT: "Right",
    ButtonId.BACK: "Esc",
    ButtonId.OK: "Enter",
}

COLOR_BG = (0, 0, 0)
COLOR_BOX = (40, 40, 60)
COLOR_BOX_ACTIVE = (220, 90, 30)
COLOR_BORDER = (180, 180, 200)
COLOR_TEXT = (220, 220, 220)
COLOR_TEXT_ACTIVE = (0, 0, 0)
COLOR_FOOTER = (110, 110, 130)

FONT_CANDIDATES = (
    "/System/Library/Fonts/SFNS.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str,
               font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _draw(canvas: Image.Image, states: dict[ButtonId, bool],
          label_font, key_font, footer_font) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=COLOR_BG)

    margin = 6
    footer_h = 20
    grid_h = HEIGHT - footer_h - margin * 2
    grid_w = WIDTH - margin * 2

    rows, cols = len(LAYOUT), len(LAYOUT[0])
    cell_w, cell_h = grid_w // cols, grid_h // rows

    for r, row in enumerate(LAYOUT):
        for c, bid in enumerate(row):
            x0 = margin + c * cell_w + 3
            y0 = margin + r * cell_h + 3
            x1 = x0 + cell_w - 6
            y1 = y0 + cell_h - 6
            pressed = states.get(bid, False)
            fill = COLOR_BOX_ACTIVE if pressed else COLOR_BOX
            text_color = COLOR_TEXT_ACTIVE if pressed else COLOR_TEXT
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=COLOR_BORDER)

            label = bid.value.upper()
            key = KEY_LABEL[bid]
            lw, lh = _text_size(draw, label, label_font)
            pw, ph = _text_size(draw, key, key_font)
            block_h = lh + 2 + ph
            inner_w, inner_h = cell_w - 6, cell_h - 6
            base_y = y0 + (inner_h - block_h) // 2

            draw.text((x0 + (inner_w - lw) // 2, base_y),
                      label, fill=text_color, font=label_font)
            draw.text((x0 + (inner_w - pw) // 2, base_y + lh + 2),
                      key, fill=text_color, font=key_font)

    footer = "Ctrl+C to quit  |  hold Enter+Esc 1s to quit"
    draw.text((margin, HEIGHT - footer_h + 4),
              footer, fill=COLOR_FOOTER, font=footer_font)


def main() -> int:
    display = TkDisplay(scale=2)
    panel = TkButtonPanel()
    canvas = new_canvas()

    label_font = _load_font(20)
    key_font = _load_font(12)
    footer_font = _load_font(11)

    period = 1.0 / REFRESH_HZ
    exit_held_since: float | None = None

    try:
        while True:
            states = panel.states()
            _draw(canvas, states, label_font, key_font, footer_font)
            display.blit(canvas)

            if states[ButtonId.OK] and states[ButtonId.BACK]:
                if exit_held_since is None:
                    exit_held_since = time.monotonic()
                elif time.monotonic() - exit_held_since >= EXIT_HOLD_S:
                    print("Exit combo held — quitting.")
                    return 0
            else:
                exit_held_since = None

            time.sleep(period)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        panel.close()
        display.close()


if __name__ == "__main__":
    raise SystemExit(main())
