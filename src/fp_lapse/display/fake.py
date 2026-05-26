"""In-memory `Display` fake for headless tests and screenshot capture.

`InMemoryDisplay` satisfies the same `Display` Protocol as `Framebuffer`
and `TkDisplay`, but stores blitted frames in memory and can dump them to
PNG. Used to verify UI rendering without a real screen — pixels go to a
file we can open and look at.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from PIL import Image

from .iface import HEIGHT, WIDTH


class InMemoryDisplay:
    """Stores blitted frames in memory; can dump them as PNG."""

    width = WIDTH
    height = HEIGHT

    def __init__(self) -> None:
        self.last_frame: Optional[Image.Image] = None
        self.frame_count: int = 0
        self._closed: bool = False

    # --- Protocol surface ---
    def blit(self, img: Image.Image) -> None:
        if self._closed:
            raise RuntimeError("InMemoryDisplay is closed")
        if img.size != (self.width, self.height):
            raise ValueError(
                f"image is {img.size}, display is {self.width}x{self.height}"
            )
        # Copy so the caller can mutate the canvas without changing our record.
        self.last_frame = img.copy()
        self.frame_count += 1

    def close(self) -> None:
        self._closed = True

    # --- Test-only helpers ---
    def save_last_frame(self, path: Union[str, Path], scale: int = 1) -> Path:
        """Write the most recent frame to `path` as PNG.

        `scale` upscales the saved image with nearest-neighbour for easier
        visual inspection of the tiny 320x240 layout. The internal
        last_frame is not changed.
        """
        if self.last_frame is None:
            raise RuntimeError("no frame has been blitted yet")
        if scale < 1:
            raise ValueError("scale must be >= 1")
        out = Path(path)
        img = self.last_frame
        if scale != 1:
            img = img.resize(
                (self.width * scale, self.height * scale),
                Image.NEAREST,
            )
        img.save(out, "PNG")
        return out
