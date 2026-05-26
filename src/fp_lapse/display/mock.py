"""Tk-based mock Display for Mac development.

Renders the same 320x240 PIL canvas in a Tk window, optionally upscaled
(default 2x = 640x480) so it's actually visible on a high-DPI Mac screen.
Tkinter ships in the stdlib; only PIL.ImageTk is needed (part of Pillow).

Use:
    from fp_lapse.display.mock import TkDisplay
    display = TkDisplay()
    display.blit(img)
    ...
    display.close()

Sharing the Tk root with the buttons mock is handled by
`fp_lapse._tk_root`: when both mocks are created in the same process they
land in the same window automatically.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from PIL import Image, ImageTk

from .. import _tk_root
from .iface import HEIGHT, WIDTH


class TkDisplay:
    """In-window mock display."""

    width = WIDTH
    height = HEIGHT

    def __init__(self, scale: int = 2) -> None:
        if scale < 1:
            raise ValueError("scale must be >= 1")
        self.scale = scale
        root = _tk_root.get()
        self._frame = tk.Frame(root)
        self._frame.pack(padx=8, pady=8)
        # Canvas with explicit dimensions — a Label sized to its image content
        # only resolves correctly after the first idle cycle, which leaves the
        # window at minimum size on macOS Tk 8.5.
        self._canvas = tk.Canvas(
            self._frame,
            width=self.width * self.scale,
            height=self.height * self.scale,
            bg="black",
            highlightthickness=0,
        )
        self._canvas.pack()
        self._image_id: Optional[int] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        _tk_root.pump()

    def blit(self, img: Image.Image) -> None:
        if img.size != (self.width, self.height):
            raise ValueError(
                f"image is {img.size}, display is {self.width}x{self.height}"
            )
        if self.scale != 1:
            shown = img.resize(
                (self.width * self.scale, self.height * self.scale),
                Image.NEAREST,
            )
        else:
            shown = img
        # Keep a reference; Tk would otherwise garbage-collect the PhotoImage.
        self._photo = ImageTk.PhotoImage(shown)
        if self._image_id is None:
            self._image_id = self._canvas.create_image(
                0, 0, anchor=tk.NW, image=self._photo,
            )
        else:
            self._canvas.itemconfigure(self._image_id, image=self._photo)
        _tk_root.pump()

    def close(self) -> None:
        try:
            self._frame.destroy()
        except Exception:
            pass
