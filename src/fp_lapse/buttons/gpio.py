"""Real `ButtonPanel` adapter — GPIO via gpiozero.

The six buttons on the Geekworm pitft22 HAT are active-low with internal
pull-ups; mechanical bounce was observed on at least one of them, so every
button is software-debounced.

Mapping verified with gpiomon on Bookworm + RPi 3. The functional roles
(UP/DOWN/etc.) are provisional and may need to be swapped once we confirm
the physical disposition of the cross.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict

# gpiozero's legacy RPi.GPIO backend can't do edge detection on recent
# Raspberry Pi OS kernels ("Failed to add edge detection"). Force the
# modern lgpio backend; must be set before gpiozero resolves its factory.
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")

from gpiozero import Button  # noqa: E402

from .iface import ButtonId

DEBOUNCE_S: float = 0.05

BUTTON_PINS: Dict[ButtonId, int] = {
    ButtonId.UP: 23,
    ButtonId.DOWN: 22,
    ButtonId.LEFT: 24,
    ButtonId.RIGHT: 5,
    ButtonId.BACK: 17,
    ButtonId.OK: 4,
}


@dataclass
class GpioButtonPanel:
    """Real `ButtonPanel` over gpiozero. Satisfies the `ButtonPanel` Protocol."""

    buttons: Dict[ButtonId, Button]

    @classmethod
    def create(cls) -> "GpioButtonPanel":
        return cls(buttons={
            bid: Button(pin, pull_up=True, bounce_time=DEBOUNCE_S)
            for bid, pin in BUTTON_PINS.items()
        })

    def is_pressed(self, bid: ButtonId) -> bool:
        return self.buttons[bid].is_pressed

    def states(self) -> Dict[ButtonId, bool]:
        return {bid: btn.is_pressed for bid, btn in self.buttons.items()}

    def on_press(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self.buttons[bid].when_pressed = callback

    def on_release(self, bid: ButtonId, callback: Callable[[], None]) -> None:
        self.buttons[bid].when_released = callback

    def close(self) -> None:
        for btn in self.buttons.values():
            btn.close()

    def __enter__(self) -> "GpioButtonPanel":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
