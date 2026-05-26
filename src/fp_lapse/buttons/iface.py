"""Button panel abstraction — Protocol and shared types.

The same six logical buttons (UP, DOWN, LEFT, RIGHT, BACK, OK) are exposed
by every adapter. On the Pi they map to GPIO pins via gpiozero; on the Mac
they map to keystrokes captured by the Tk window.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, Protocol


class ButtonId(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    BACK = "back"   # lateral superior — menu / back
    OK = "ok"       # lateral inferior — select / OK


class ButtonPanel(Protocol):
    def is_pressed(self, bid: ButtonId) -> bool: ...
    def states(self) -> Dict[ButtonId, bool]: ...
    def on_press(self, bid: ButtonId, callback: Callable[[], None]) -> None: ...
    def on_release(self, bid: ButtonId, callback: Callable[[], None]) -> None: ...
    def close(self) -> None: ...
