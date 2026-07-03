"""Tests for the discrete value lists the edit screen cycles through.

Focus: the ISO list. Regression for the bug where native 1/3-EV ISO
values (notably ISO 640) were unreachable because the UI only exposed
full stops (100, 200, 400, …). The exposed list must match the Sigma
fp's native 1/3-stop ISO scale within the JSON validator bounds, so the
UI never offers a value the camera would silently snap to a neighbour.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.configs import ISO_MAX, ISO_MIN, MAX_SHOTS_PER_BRACKET  # noqa: E402
from fp_lapse.ui.edit_values import (  # noqa: E402
    BRACKET_N_VALUES,
    DIRECTION_BRIGHTEST,
    DIRECTION_DARKEST,
    DIRECTION_VALUES,
    EV_STEP_VALUES,
    ISO2_OFF,
    ISO2_VALUES,
    ISO_VALUES,
    cycle_in_list,
    format_direction,
    format_ev_step,
    format_iso2,
)

# Sigma fp native 1/3-EV ISO scale, 100..25600 (matches the
# ISOSpeedConverter APEX table in sigma-ptpy exactly — every value here
# round-trips through the camera without snapping).
NATIVE_THIRDS_100_25600 = [
    100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600,
    2000, 2500, 3200, 4000, 5000, 6400, 8000, 10000, 12800, 16000,
    20000, 25600,
]


class TestIsoValues(unittest.TestCase):
    def test_iso_640_is_selectable(self):
        # The bug: 640 is a native fp ISO but was not in the cycling list.
        self.assertIn(640, ISO_VALUES)

    def test_iso_list_is_native_thirds_scale(self):
        # Pins the decision: the full native 1/3-stop scale, no full-stop
        # gaps, no extended low (<100) / high (>25600) ranges.
        self.assertEqual(ISO_VALUES, NATIVE_THIRDS_100_25600)

    def test_iso_values_within_validator_bounds(self):
        # Every offered value must pass configs.validate_strict.
        for iso in ISO_VALUES:
            self.assertGreaterEqual(iso, ISO_MIN)
            self.assertLessEqual(iso, ISO_MAX)

    def test_iso_values_strictly_increasing(self):
        self.assertEqual(ISO_VALUES, sorted(set(ISO_VALUES)))

    def test_cycle_reaches_640_from_500(self):
        self.assertEqual(cycle_in_list(500, ISO_VALUES, 1), 640)


class TestEvStepValues(unittest.TestCase):
    def test_exact_set(self):
        self.assertEqual(EV_STEP_VALUES, [1, 2, 2.5, 3, 3.5, 4])

    def test_half_steps_present(self):
        self.assertIn(2.5, EV_STEP_VALUES)
        self.assertIn(3.5, EV_STEP_VALUES)

    def test_cycle_wraps(self):
        self.assertEqual(cycle_in_list(4, EV_STEP_VALUES, 1), 1)
        self.assertEqual(cycle_in_list(2, EV_STEP_VALUES, 1), 2.5)

    def test_format_integer_steps_no_trailing_decimal(self):
        self.assertEqual(format_ev_step(1), "1 EV")
        self.assertEqual(format_ev_step(2), "2 EV")
        self.assertEqual(format_ev_step(4), "4 EV")

    def test_format_half_steps(self):
        self.assertEqual(format_ev_step(2.5), "2.5 EV")
        self.assertEqual(format_ev_step(3.5), "3.5 EV")


class TestBracketNValues(unittest.TestCase):
    def test_one_to_max_no_auto(self):
        self.assertEqual(BRACKET_N_VALUES, list(range(1, MAX_SHOTS_PER_BRACKET + 1)))
        self.assertEqual(BRACKET_N_VALUES[0], 1)
        self.assertEqual(BRACKET_N_VALUES[-1], MAX_SHOTS_PER_BRACKET)
        self.assertNotIn("auto", BRACKET_N_VALUES)

    def test_cycle_wraps_at_nine(self):
        self.assertEqual(cycle_in_list(9, BRACKET_N_VALUES, 1), 1)


class TestDirectionValues(unittest.TestCase):
    def test_two_sentinels(self):
        self.assertEqual(DIRECTION_VALUES, [DIRECTION_BRIGHTEST, DIRECTION_DARKEST])

    def test_cycle_toggles(self):
        self.assertEqual(
            cycle_in_list(DIRECTION_BRIGHTEST, DIRECTION_VALUES, 1),
            DIRECTION_DARKEST,
        )
        self.assertEqual(
            cycle_in_list(DIRECTION_DARKEST, DIRECTION_VALUES, 1),
            DIRECTION_BRIGHTEST,
        )

    def test_format(self):
        self.assertEqual(format_direction(DIRECTION_BRIGHTEST), "brightest")
        self.assertEqual(format_direction(DIRECTION_DARKEST), "darkest")


class TestIso2Values(unittest.TestCase):
    def test_off_sentinel_first(self):
        self.assertEqual(ISO2_VALUES[0], ISO2_OFF)
        self.assertEqual(ISO2_VALUES[1:], ISO_VALUES)

    def test_cycle_into_off(self):
        # Cycling forward from the highest ISO wraps to the off sentinel.
        self.assertEqual(cycle_in_list(ISO_VALUES[-1], ISO2_VALUES, 1), ISO2_OFF)
        # Cycling back from off lands on the highest ISO.
        self.assertEqual(cycle_in_list(ISO2_OFF, ISO2_VALUES, -1), ISO_VALUES[-1])

    def test_format(self):
        self.assertEqual(format_iso2(ISO2_OFF), "off")
        self.assertEqual(format_iso2(400), "400")


if __name__ == "__main__":
    unittest.main()
