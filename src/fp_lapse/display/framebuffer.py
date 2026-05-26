"""Framebuffer driver: pushes PIL RGB images to the TFT panel as RGB565.

The pitft22 overlay registers the 2.2" ILI9340 panel as an fbtft
framebuffer. Its index is not fixed across OS versions: on Raspberry Pi
OS Bookworm it came up as /dev/fb0, but on Trixie a firmware
simple-framebuffer claims fb0 and the panel lands on /dev/fb1. We locate
it by driver name (see `find_panel_device`) so the index doesn't matter.
With rotate=270 the visible orientation is landscape 320x240.

This satisfies the `Display` Protocol from `.iface`. Pi-only — needs the
panel framebuffer to exist and be writable (group `video` or root).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np
from PIL import Image

from .iface import HEIGHT, WIDTH

FB_DEVICE: Final[Path] = Path("/dev/fb0")
BYTES_PER_PIXEL: Final[int] = 2  # RGB565
PANEL_DRIVER: Final[str] = "ili9340"  # fbtft driver name of the pitft22 panel


def find_panel_device(driver: str = PANEL_DRIVER) -> Path:
    """Locate the TFT panel's framebuffer by fbtft driver name.

    The panel's framebuffer index is not fixed across OS versions: on
    Bookworm the pitft22 overlay came up as /dev/fb0, but on Trixie a
    firmware simple-framebuffer claims fb0 and the panel lands on
    /dev/fb1. Scan /sys/class/graphics/fb*/name for the driver so the
    index doesn't matter; fall back to /dev/fb0 (e.g. on hosts without
    the panel, like Mac dev).
    """
    try:
        nodes = sorted(Path("/sys/class/graphics").glob("fb[0-9]*"))
    except OSError:
        nodes = []
    for node in nodes:
        try:
            if driver in (node / "name").read_text():
                return Path("/dev") / node.name
        except OSError:
            continue
    return FB_DEVICE


def rgb888_to_rgb565(img: Image.Image) -> bytes:
    """Pack a PIL RGB image into little-endian RGB565 bytes.

    Vectorized via numpy — the pure-Python fallback (looping 76 800
    pixels) took ~325 ms per frame on a Pi 3, which monopolised the
    main loop and caused the engine to miss grid instants. numpy brings
    that down to <20 ms.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.asarray(img, dtype=np.uint16)
    r = (arr[..., 0] >> 3) & 0x1F
    g = (arr[..., 1] >> 2) & 0x3F
    b = (arr[..., 2] >> 3) & 0x1F
    rgb565 = (r << 11) | (g << 5) | b
    return rgb565.astype("<u2").tobytes()


class Framebuffer:
    """Direct framebuffer writer for a single 320x240 RGB565 panel."""

    def __init__(
        self,
        path: Path | None = None,
        width: int = WIDTH,
        height: int = HEIGHT,
    ) -> None:
        self.path = path if path is not None else find_panel_device()
        self.width = width
        self.height = height
        self._fp = open(self.path, "r+b", buffering=0)

    def blit(self, img: Image.Image) -> None:
        if img.size != (self.width, self.height):
            raise ValueError(
                f"image is {img.size}, framebuffer is {self.width}x{self.height}"
            )
        self._fp.seek(0)
        self._fp.write(rgb888_to_rgb565(img))

    def close(self) -> None:
        self._fp.close()

    def __enter__(self) -> "Framebuffer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
