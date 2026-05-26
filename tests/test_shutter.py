"""Tests for `parse_shutter` and `format_shutter`. Stdlib unittest."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.shutter import (  # noqa: E402
    SHUTTER_MAX_S,
    SHUTTER_MIN_S,
    ShutterValueError,
    format_shutter,
    in_range,
    parse_shutter,
)


class TestParseShutter(unittest.TestCase):
    def test_null_rejected(self):
        # `None` and `"auto"` used to be valid placeholders for "leave the
        # camera as is" / "automatic shutter". The data model no longer
        # encodes auto at the parameter level (it lives in
        # `TimelapseConfig.shots == ()`), so parse_shutter must reject
        # them as invalid input.
        with self.assertRaises(ShutterValueError):
            parse_shutter(None)

    def test_auto_rejected(self):
        with self.assertRaises(ShutterValueError):
            parse_shutter("auto")

    def test_fraction_strings(self):
        self.assertAlmostEqual(parse_shutter("1/500"), 0.002)
        self.assertAlmostEqual(parse_shutter("1/8000"), 1 / 8000)
        self.assertAlmostEqual(parse_shutter(" 1 / 2 "), 0.5)

    def test_decimal_strings(self):
        self.assertEqual(parse_shutter("0.5"), 0.5)
        self.assertEqual(parse_shutter("2"), 2.0)
        self.assertEqual(parse_shutter("30"), 30.0)

    def test_numeric(self):
        self.assertEqual(parse_shutter(0.5), 0.5)
        self.assertEqual(parse_shutter(2), 2.0)
        self.assertEqual(parse_shutter(30), 30.0)

    def test_fraction_must_have_numerator_1(self):
        with self.assertRaises(ShutterValueError):
            parse_shutter("2/3")

    def test_invalid_strings_raise(self):
        for bad in ["abc", "1/", "/500", "", "1/abc"]:
            with self.subTest(bad=bad), self.assertRaises(ShutterValueError):
                parse_shutter(bad)

    def test_non_positive_rejected(self):
        for bad in [0, -1, "0", "-0.5", "1/-2"]:
            with self.subTest(bad=bad), self.assertRaises(ShutterValueError):
                parse_shutter(bad)

    def test_bool_explicitly_rejected(self):
        with self.assertRaises(ShutterValueError):
            parse_shutter(True)


class TestInRange(unittest.TestCase):
    def test_min_and_max_included(self):
        self.assertTrue(in_range(SHUTTER_MIN_S))
        self.assertTrue(in_range(SHUTTER_MAX_S))

    def test_outside_rejected(self):
        self.assertFalse(in_range(SHUTTER_MIN_S / 2))
        self.assertFalse(in_range(SHUTTER_MAX_S * 2))


class TestFormatShutter(unittest.TestCase):
    def test_clean_fractions(self):
        self.assertEqual(format_shutter(0.002), "1/500")
        self.assertEqual(format_shutter(1 / 8000), "1/8000")
        self.assertEqual(format_shutter(0.5), "1/2")
        self.assertEqual(format_shutter(0.125), "1/8")

    def test_integer_seconds(self):
        self.assertEqual(format_shutter(1.0), "1 s")
        self.assertEqual(format_shutter(2.0), "2 s")
        self.assertEqual(format_shutter(30.0), "30 s")

    def test_non_clean_subsecond(self):
        # 0.333 isn't 1/3 exactly → falls back to decimal form
        self.assertEqual(format_shutter(0.333), "0.333 s")

    def test_fractional_over_one(self):
        self.assertEqual(format_shutter(1.5), "1.5 s")


if __name__ == "__main__":
    unittest.main()
