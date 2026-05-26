"""Font loading with Mac → Pi → default-bitmap fallback.

The PiTFT 2.2" renders RGB565 at 320x240, so readability needs a
clean-pixel monospace font (Menlo on Mac, DejaVuSansMono on the Pi).
The mockups in `docs/mockups/` were generated with Menlo; the visual
regression tests assume the same environment (Mac). On the Pi we use
DejaVu as a reasonable proxy; the differences stay within acceptable
UI margins.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import ImageFont

# Probe order: closest-to-Menlo first. If none loads, PIL falls back
# to its default bitmap (readable but ugly).
_MONO_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",                              # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",          # Debian/Pi
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)

_PROPORTIONAL_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",                          # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _first_existing(paths: Iterable[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


@lru_cache(maxsize=32)
def mono(size: int) -> ImageFont.ImageFont:
    """Monospace for config / shot / button listings."""
    path = _first_existing(_MONO_CANDIDATES)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=32)
def proportional(size: int) -> ImageFont.ImageFont:
    """Proportional for titles / long-form text."""
    path = _first_existing(_PROPORTIONAL_CANDIDATES)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)
