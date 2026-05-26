"""Tk-based mock `ButtonPanel` — captures key events for Mac development.

Keymap (held = pressed):

    Arrow Up    → UP
    Arrow Down  → DOWN
    Arrow Left  → LEFT
    Arrow Right → RIGHT
    Enter       → OK
    Escape      → BACK

Shares the singleton Tk root with `display.mock.TkDisplay`, so when both
mocks are created in the same process the buttons capture keystrokes on the
same window the framebuffer is rendered to.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, Optional

from .. import _tk_root
from .iface import ButtonId

# Tk keysyms → ButtonId.
KEYMAP: Dict[str, ButtonId] = {
    "Up": ButtonId.UP,
    "Down": ButtonId.DOWN,
    "Left": ButtonId.LEFT,
    "Right": ButtonId.RIGHT,
    "Return": ButtonId.OK,
    "KP_Enter": ButtonId.OK,
    "Escape": ButtonId.BACK,
}


class TkButtonPanel:
    """Mock `ButtonPanel` driven by keyboard events on the shared Tk root.

    Keyboard autorepeat: Tk on Mac and on X11 fires repeated
    `KeyRelease`/`KeyPress` pairs every ~30 ms while a key is held.
    If you let those through, a 3-second hold of Enter (OK long-press
    → manage menu) translates into dozens of short presses → the main
    screen starts the timelapse on the first release before `tick()`
    can cross the 3-second threshold.

    Fix: debounce the release. When `KeyRelease` arrives, the actual
    release callback is scheduled with `_RELEASE_DEBOUNCE_MS` delay.
    If a `KeyPress` for the same key arrives before then (= an
    autorepeat), the release is cancelled and that press is also
    ignored (it's a continuation, not a new press).
    """

    _RELEASE_DEBOUNCE_MS: int = 50

    def __init__(self) -> None:
        root = _tk_root.get()
        self._root = root
        self._pressed: Dict[ButtonId, bool] = {bid: False for bid in ButtonId}
        self._on_press: Dict[ButtonId, Optional[Callable[[], None]]] = {
            bid: None for bid in ButtonId
        }
        self._on_release: Dict[ButtonId, Optional[Callable[[], None]]] = {
            bid: None for bid in ButtonId
        }
        self._pending_release: Dict[ButtonId, Optional[str]] = {
            bid: None for bid in ButtonId
        }
        root.bind("<KeyPress>", self._on_keypress)
        root.bind("<KeyRelease>", self._on_keyrelease)
        # Make sure the root has focus so it actually receives key events.
        root.focus_force()

    def _on_keypress(self, event: tk.Event) -> None:
        bid = KEYMAP.get(event.keysym)
        if bid is None:
            return
        # If a release was pending, this was autorepeat: cancel the
        # release and do NOT fire `on_press` again (the key was still
        # held from before).
        pending = self._pending_release[bid]
        if pending is not None:
            try:
                self._root.after_cancel(pending)
            except Exception:
                pass
            self._pending_release[bid] = None
            return
        if self._pressed[bid]:
            return
        self._pressed[bid] = True
        cb = self._on_press[bid]
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _on_keyrelease(self, event: tk.Event) -> None:
        bid = KEYMAP.get(event.keysym)
        if bid is None or not self._pressed[bid]:
            return
        # If a release is already scheduled, leave it running (don't
        # reschedule on every autorepeat); when its `after` fires it
        # will trigger the real release.
        if self._pending_release[bid] is not None:
            return
        after_id = self._root.after(
            self._RELEASE_DEBOUNCE_MS, lambda b=bid: self._fire_release(b)
        )
        self._pending_release[bid] = after_id

    def _fire_release(self, bid: ButtonId) -> None:
        self._pending_release[bid] = None
        if not self._pressed[bid]:
            return
        self._pressed[bid] = False
        cb = self._on_release[bid]
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def is_pressed(self, bid: ButtonId) -> bool:
        _tk_root.pump()
        return self._pressed[bid]

    def states(self) -> Dict[ButtonId, bool]:
        _tk_root.pump()
        return dict(self._pressed)

    def on_press(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self._on_press[bid] = callback

    def on_release(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self._on_release[bid] = callback

    def close(self) -> None:
        try:
            self._root.unbind("<KeyPress>")
            self._root.unbind("<KeyRelease>")
        except Exception:
            pass

    def __enter__(self) -> "TkButtonPanel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
