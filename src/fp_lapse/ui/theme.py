"""Palette and visual constants for the intervalometer.

Calibrated in the mockups session on 2026-05-20 for maximum contrast
on the PiTFT 2.2" panel (RGB565 little-endian, 320x240). Any change
that breaks the mockups in `docs/mockups/` requires regenerating the
PNGs (`docs/mockups/render_mockups.py`) and reviewing the screens
visually.
"""

from __future__ import annotations

from typing import Final, Tuple

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]

# Background / foreground
BG: Final[RGB] = (10, 14, 20)
FG: Final[RGB] = (235, 235, 230)
DIM: Final[RGB] = (130, 130, 135)
SEP: Final[RGB] = (45, 50, 60)

# Selection band (inverse video)
SEL_BG: Final[RGB] = (215, 215, 215)
SEL_FG: Final[RGB] = (10, 10, 14)
SEL_DIM: Final[RGB] = (90, 90, 95)
SEL_BAR: Final[RGB] = (255, 200, 0)

# Status indicators
RUN_DOT: Final[RGB] = (240, 60, 60)
OK_DOT: Final[RGB] = (90, 200, 90)
WARN: Final[RGB] = (240, 200, 60)
ERR: Final[RGB] = (240, 60, 60)

# Modal overlays
DIALOG_BG: Final[RGB] = (28, 32, 42)
DIALOG_BORDER: Final[RGB] = (90, 95, 110)

# Columns of each shot row (in pixels from the left margin).
# Tuned against Menlo 11px: the shot index (1 character) fits between
# COL_IDX..COL_SHUT, leaving ~2 chars of gap before the shutter.
COL_IDX: Final[int] = 14
COL_SHUT: Final[int] = 32
COL_ISO: Final[int] = 112
COL_APER: Final[int] = 208

# Row heights: body, header, status bar separator, footer.
ROW_HEIGHT: Final[int] = 12
HEADER_HEIGHT: Final[int] = 13
STATUS_BAR_Y_LINE: Final[int] = 18  # separator y
# Footer is two mono-11 rows: primary action hint on top, secondary
# (global LEFT/RIGHT/chord) on the bottom. Single-line callers
# (edit_screen, picker_datetime) keep using the top row only and the
# bottom row stays empty for them — same as a tall blank footer.
FOOTER_HEIGHT: Final[int] = 28
