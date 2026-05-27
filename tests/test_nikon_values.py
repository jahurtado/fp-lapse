"""Tests for the pure value-translation helpers in `nikon_gphoto` support.

These translate the human-friendly values exposed by the `Camera` Protocol
(shutter in seconds, ISO as an int, aperture as an f-number) to/from the
**gphoto2 choice-string labels** the D5600 exposes for `shutterspeed`,
`iso` and `f-number`.

Crucially they are **pure** and import NO hardware library: they operate on
the list of choice strings supplied by the caller, so they run on the Mac
with neither `gphoto2` nor a camera attached.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.camera.nikon_values import (  # noqa: E402
    aperture_to_label,
    iso_to_label,
    label_to_aperture,
    label_to_iso,
    label_to_seconds,
    seconds_to_label,
)


# gphoto2 D5600 choice lists captured during the 2026-05-27 spike (trimmed).
SHUTTER_CHOICES = [
    "Bulb", "Time",
    "30.0000s", "25.0000s", "20.0000s", "15.0000s", "13.0000s", "10.0000s",
    "8.0000s", "6.0000s", "5.0000s", "4.0000s", "3.0000s", "2.5000s",
    "2.0000s", "1.6000s", "1.3000s", "1.0000s", "0.8000s", "0.6000s",
    "0.5000s", "0.4000s", "0.3000s", "0.2500s", "0.2000s", "0.1666s",
    "0.1250s", "0.1000s", "0.0769s", "0.0625s", "0.0500s", "0.0400s",
    "0.0333s", "0.0250s", "0.0200s", "0.0166s", "0.0125s", "0.0100s",
    "0.0080s", "0.0062s", "0.0050s", "0.0040s", "0.0033s", "0.0025s",
    "0.0020s", "0.0015s", "0.0012s", "0.0010s", "0.0008s", "0.0006s",
    "0.0005s", "0.0004s", "0.0003s", "0.0002s",
]
ISO_CHOICES = ["100", "200", "400", "800", "1600", "3200", "6400", "12800", "25600"]
ISO_CHOICES_AUTO = ["Auto"] + ISO_CHOICES
APERTURE_CHOICES = [
    "f/3.5", "f/4", "f/4.5", "f/5", "f/5.6", "f/6.3", "f/7.1", "f/8",
    "f/9", "f/10", "f/11", "f/13", "f/14", "f/16", "f/18", "f/20", "f/22",
]


class TestSecondsToLabel(unittest.TestCase):
    def test_spike_example_0_002(self):
        # The spike: 0.002 s requested → "0.0020s" choice (EXIF read 1/500).
        self.assertEqual(seconds_to_label(0.002, SHUTTER_CHOICES), "0.0020s")

    def test_exact_30s(self):
        self.assertEqual(seconds_to_label(30.0, SHUTTER_CHOICES), "30.0000s")

    def test_nearest_picks_closest_choice(self):
        # 1/500 = 0.002 exactly; 1/498 should still snap to the nearest label.
        self.assertEqual(seconds_to_label(1 / 498, SHUTTER_CHOICES), "0.0020s")

    def test_one_thirtieth(self):
        # 1/30 = 0.0333... → "0.0333s" is the closest label.
        self.assertEqual(seconds_to_label(1 / 30, SHUTTER_CHOICES), "0.0333s")

    def test_bulb_and_time_excluded_as_targets(self):
        # A huge requested value must NOT snap to "Bulb"/"Time"; it clamps to
        # the largest numeric choice (30s).
        self.assertEqual(seconds_to_label(120.0, SHUTTER_CHOICES), "30.0000s")

    def test_below_range_clamps_to_smallest(self):
        self.assertEqual(seconds_to_label(0.00001, SHUTTER_CHOICES), "0.0002s")

    def test_no_numeric_choices_returns_none(self):
        self.assertIsNone(seconds_to_label(0.002, ["Bulb", "Time"]))


class TestLabelToSeconds(unittest.TestCase):
    def test_parse_decimal_label(self):
        self.assertAlmostEqual(label_to_seconds("0.0020s"), 0.002)

    def test_parse_whole_seconds(self):
        self.assertAlmostEqual(label_to_seconds("30.0000s"), 30.0)

    def test_bulb_returns_none(self):
        self.assertIsNone(label_to_seconds("Bulb"))

    def test_time_returns_none(self):
        self.assertIsNone(label_to_seconds("Time"))

    def test_garbage_returns_none(self):
        self.assertIsNone(label_to_seconds("???"))


class TestIsoToLabel(unittest.TestCase):
    def test_spike_example_800(self):
        self.assertEqual(iso_to_label(800, ISO_CHOICES), "800")

    def test_nearest_int(self):
        # 900 is closer to 800 than to 1600.
        self.assertEqual(iso_to_label(900, ISO_CHOICES), "800")

    def test_nearest_int_rounds_up(self):
        # 1300 is closer to 1600 than to 800.
        self.assertEqual(iso_to_label(1300, ISO_CHOICES), "1600")

    def test_auto_choice_not_matched_as_number(self):
        self.assertEqual(iso_to_label(800, ISO_CHOICES_AUTO), "800")

    def test_clamps_high(self):
        self.assertEqual(iso_to_label(99999, ISO_CHOICES), "25600")

    def test_no_numeric_choices_returns_none(self):
        self.assertIsNone(iso_to_label(800, ["Auto"]))


class TestLabelToIso(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(label_to_iso("800"), 800)

    def test_auto_returns_none(self):
        self.assertIsNone(label_to_iso("Auto"))

    def test_garbage_returns_none(self):
        self.assertIsNone(label_to_iso("xyz"))


class TestApertureToLabel(unittest.TestCase):
    def test_spike_example_5_6(self):
        self.assertEqual(aperture_to_label(5.6, APERTURE_CHOICES), "f/5.6")

    def test_nearest(self):
        # 5.5 is between f/5 and f/5.6 → closer to f/5.6 (0.1 vs 0.5).
        self.assertEqual(aperture_to_label(5.5, APERTURE_CHOICES), "f/5.6")

    def test_whole_fstop_label(self):
        self.assertEqual(aperture_to_label(8.0, APERTURE_CHOICES), "f/8")

    def test_clamps_high(self):
        self.assertEqual(aperture_to_label(64.0, APERTURE_CHOICES), "f/22")

    def test_no_numeric_choices_returns_none(self):
        self.assertIsNone(aperture_to_label(5.6, ["Unknown"]))


class TestLabelToAperture(unittest.TestCase):
    def test_parse_decimal(self):
        self.assertAlmostEqual(label_to_aperture("f/5.6"), 5.6)

    def test_parse_whole(self):
        self.assertAlmostEqual(label_to_aperture("f/8"), 8.0)

    def test_garbage_returns_none(self):
        self.assertIsNone(label_to_aperture("nope"))


if __name__ == "__main__":
    unittest.main()
