"""In-memory `ButtonPanel` fake for headless tests.

`FakeButtonPanel` satisfies the same `ButtonPanel` Protocol as
`GpioButtonPanel` and `TkButtonPanel`, but exposes test-only `press()` /
`release()` / `tap()` / `hold()` methods so test code can drive button
events programmatically. Also counts presses per button for assertions.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional

from .iface import ButtonId


class FakeButtonPanel:
    """Pure in-memory `ButtonPanel`. No GUI, no GPIO."""

    def __init__(self) -> None:
        self._pressed: Dict[ButtonId, bool] = {bid: False for bid in ButtonId}
        self._on_press: Dict[ButtonId, Optional[Callable[[], None]]] = {
            bid: None for bid in ButtonId
        }
        self._on_release: Dict[ButtonId, Optional[Callable[[], None]]] = {
            bid: None for bid in ButtonId
        }
        self.press_count: Dict[ButtonId, int] = {bid: 0 for bid in ButtonId}

    # --- Protocol surface ---
    def is_pressed(self, bid: ButtonId) -> bool:
        return self._pressed[bid]

    def states(self) -> Dict[ButtonId, bool]:
        return dict(self._pressed)

    def on_press(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self._on_press[bid] = callback

    def on_release(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self._on_release[bid] = callback

    def close(self) -> None:
        pass

    # --- Test-only API: drive events ---
    def press(self, bid: ButtonId) -> None:
        """Mark `bid` as pressed; fire on_press if registered. No-op if
        already pressed (matches real-device debounce-deduplicated behaviour)."""
        if self._pressed[bid]:
            return
        self._pressed[bid] = True
        self.press_count[bid] += 1
        cb = self._on_press[bid]
        if cb is not None:
            cb()

    def release(self, bid: ButtonId) -> None:
        """Mark `bid` as released; fire on_release if registered."""
        if not self._pressed[bid]:
            return
        self._pressed[bid] = False
        cb = self._on_release[bid]
        if cb is not None:
            cb()

    def tap(self, bid: ButtonId) -> None:
        """Press then release synchronously."""
        self.press(bid)
        self.release(bid)

    def hold(self, bid: ButtonId, duration_s: float) -> None:
        """Press, sleep `duration_s`, release. Blocks the caller."""
        self.press(bid)
        time.sleep(duration_s)
        self.release(bid)
