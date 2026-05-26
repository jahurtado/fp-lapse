"""Regression tests for `fp_lapse.__main__` wiring.

These don't exercise the full loop — they pin the contract between
`__main__` and the adapter modules so that a wiring bug (wrong factory
name, missing constructor argument, etc.) is caught at unit-test time
instead of at boot on the Pi.
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse import __main__ as main_mod  # noqa: E402


class TestBuildButtonsWiring(unittest.TestCase):
    def test_gpio_branch_uses_factory(self):
        # `GpioButtonPanel` is a frozen-ish dataclass whose generated
        # __init__ requires `buttons`. Constructing it bare crashes at
        # startup on the Pi. The hardware-correct entry point is the
        # `create()` classmethod which instantiates the gpiozero
        # `Button`s for the pin map. Pin that contract here.
        src = inspect.getsource(main_mod._build_buttons)
        self.assertIn("GpioButtonPanel.create()", src)
        self.assertNotIn(
            "GpioButtonPanel()", src,
            "must call GpioButtonPanel.create(), not the dataclass __init__",
        )


if __name__ == "__main__":
    unittest.main()
