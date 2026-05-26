#!/usr/bin/env python3
"""demo_display.py — visual smoke test for the Tk mock Display.

Opens a 640x480 Tk window (320x240 mock framebuffer upscaled 2x) and animates
a test pattern: color bars + a moving white box + a clock. Lets you confirm
visually that the mock works on your Mac before plugging the real engine /
UI into it.

Run on the Mac:
    uv run python scripts/demo_display.py

Quit: close the window or Ctrl+C in the terminal.
"""

from __future__ import annotations

import math
import os
import sys
import time

# Silence the "Tk 8.5 deprecated on macOS" warning; we know.
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from fp_lapse.display import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.display.mock import TkDisplay  # noqa: E402


REFRESH_HZ = 30

COLOR_BARS = [
    (255, 0, 0), (255, 255, 0), (0, 255, 0),
    (0, 255, 255), (0, 0, 255), (255, 0, 255),
]

FONT_CANDIDATES = (
    "/System/Library/Fonts/SFNS.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_frame(t: float,
               big_font: ImageFont.ImageFont,
               small_font: ImageFont.ImageFont) -> Image.Image:
    img = new_canvas((18, 22, 30))
    draw = ImageDraw.Draw(img)

    # Color bars.
    bar_w = WIDTH // len(COLOR_BARS)
    for i, c in enumerate(COLOR_BARS):
        draw.rectangle((i * bar_w, 0, (i + 1) * bar_w, 36), fill=c)

    # Moving box.
    cx = int(WIDTH / 2 + math.sin(t * 2) * (WIDTH / 2 - 30))
    cy = 110
    draw.rectangle((cx - 18, cy - 18, cx + 18, cy + 18),
                   fill=(240, 240, 240))

    # Clock + label.
    draw.text((10, 150), f"t = {t:5.1f}s",
              fill=(230, 230, 230), font=big_font)
    draw.text((10, 200), f"mock display {WIDTH}x{HEIGHT}",
              fill=(150, 160, 180), font=small_font)

    # Border so you can see the panel edges in the upscaled window.
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=(80, 100, 130))
    return img


def main() -> int:
    display = TkDisplay(scale=2)
    big = _load_font(24)
    small = _load_font(14)
    period = 1.0 / REFRESH_HZ
    t0 = time.monotonic()
    try:
        while True:
            display.blit(make_frame(time.monotonic() - t0, big, small))
            time.sleep(period)
    except KeyboardInterrupt:
        print()
    finally:
        display.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
