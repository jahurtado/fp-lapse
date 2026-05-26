"""Display abstraction — Protocol and shared constants.

All adapters render a PIL RGB image of WIDTH x HEIGHT pixels. The real
adapter (Framebuffer) writes RGB565 bytes to /dev/fb0; the mock (TkDisplay)
shows it in a Tk window on the developer's Mac. The rest of the system
talks only to this `Display` Protocol.
"""

from __future__ import annotations

from typing import Final, Protocol

from PIL import Image

WIDTH: Final[int] = 320
HEIGHT: Final[int] = 240


class Display(Protocol):
    width: int
    height: int

    def blit(self, img: Image.Image) -> None:
        """Render `img` (RGB, WIDTH x HEIGHT) to the panel."""
        ...

    def close(self) -> None:
        """Release the underlying resource (file handle / window)."""
        ...


def new_canvas(color: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Fresh WIDTH x HEIGHT RGB PIL image, ready to draw on."""
    return Image.new("RGB", (WIDTH, HEIGHT), color)
