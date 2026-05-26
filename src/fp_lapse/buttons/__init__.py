"""Button panel abstraction.

Two adapters satisfy the `ButtonPanel` Protocol; both are imported lazily
so the package can be loaded on a Mac without gpiozero (`gpio.py`) or on a
headless Pi without tkinter (`mock.py`).

    from fp_lapse.buttons.gpio import GpioButtonPanel, BUTTON_PINS  # Pi only
    from fp_lapse.buttons.mock import TkButtonPanel                 # Mac dev
"""

from .iface import ButtonId, ButtonPanel

__all__ = [
    "ButtonId",
    "ButtonPanel",
]
