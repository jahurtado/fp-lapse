"""Lazy singleton Tk root for the Mac-side mocks.

The display mock renders into a Tk window; the buttons mock captures key
events from the same window. Sharing one root avoids spawning two separate
windows and lets the developer interact with the UI just like on the real
device.

Tkinter ships with CPython, so no extra dependency is needed. On the Pi this
module is never imported (the real adapters live in `framebuffer.py` /
`gpio.py`); this stays out of the runtime path on the device.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

_root: Optional[tk.Tk] = None


def get() -> tk.Tk:
    """Return the shared Tk root, creating it on first call."""
    global _root
    if _root is None:
        _root = tk.Tk()
        _root.title("fp-lapse (mock)")
        _root.resizable(False, False)
    return _root


def pump() -> None:
    """Process pending Tk events. Call after each blit / state read so the
    window stays responsive without a dedicated event-loop thread."""
    if _root is None:
        return
    try:
        _root.update_idletasks()
        _root.update()
    except tk.TclError:
        # Window was destroyed by the user; further pumps no-op.
        pass
