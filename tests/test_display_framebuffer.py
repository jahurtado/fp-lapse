"""Tests for rgb888_to_rgb565 packer.

The framebuffer driver itself (the file descriptor) is hardware-only
and not unit-tested. The pure-Python packer that turns a PIL RGB image
into little-endian RGB565 bytes IS testable and matters for visual
correctness, so it gets a regression test here.

Background: this packer used to depend on numpy. Numpy 2.x has no
prebuilt armv7 wheels and compiling from sdist fails on a Pi 3 (1 GB
RAM, no `python3-dev` by default). Switching to a pure-Python
implementation removed numpy from the base dependency set. These tests
lock the byte layout so a future refactor cannot quietly change the
on-wire format.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.display.framebuffer import rgb888_to_rgb565  # noqa: E402


def _pack565(r: int, g: int, b: int) -> bytes:
    """Independent reference packer for the expected RGB565 little-endian word."""
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return struct.pack("<H", v)


class TestRgb888ToRgb565(unittest.TestCase):
    def test_single_pixel_black(self):
        img = Image.new("RGB", (1, 1), (0, 0, 0))
        self.assertEqual(rgb888_to_rgb565(img), b"\x00\x00")

    def test_single_pixel_white(self):
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        # 0xFFFF little-endian
        self.assertEqual(rgb888_to_rgb565(img), b"\xff\xff")

    def test_single_pixel_pure_red(self):
        img = Image.new("RGB", (1, 1), (255, 0, 0))
        # r=11111, g=000000, b=00000 -> 0xF800 -> little-endian 00 F8
        self.assertEqual(rgb888_to_rgb565(img), b"\x00\xf8")

    def test_single_pixel_pure_green(self):
        img = Image.new("RGB", (1, 1), (0, 255, 0))
        # g=111111 -> 0x07E0 -> LE E0 07
        self.assertEqual(rgb888_to_rgb565(img), b"\xe0\x07")

    def test_single_pixel_pure_blue(self):
        img = Image.new("RGB", (1, 1), (0, 0, 255))
        # b=11111 -> 0x001F -> LE 1F 00
        self.assertEqual(rgb888_to_rgb565(img), b"\x1f\x00")

    def test_truncation_low_bits(self):
        # 0x07 (3 bits) of red must drop entirely; 0x03 of blue must drop.
        # g=0x03 keeps only 0; net = 0.
        img = Image.new("RGB", (1, 1), (0x07, 0x03, 0x07))
        self.assertEqual(rgb888_to_rgb565(img), b"\x00\x00")

    def test_two_pixels_row_order(self):
        img = Image.new("RGB", (2, 1))
        img.putpixel((0, 0), (255, 0, 0))
        img.putpixel((1, 0), (0, 0, 255))
        # First pixel red (00 F8), second blue (1F 00).
        self.assertEqual(rgb888_to_rgb565(img), b"\x00\xf8\x1f\x00")

    def test_output_length(self):
        # 320x240 RGB image -> 320*240*2 bytes = 153600 bytes
        img = Image.new("RGB", (320, 240), (128, 128, 128))
        out = rgb888_to_rgb565(img)
        self.assertEqual(len(out), 320 * 240 * 2)

    def test_converts_non_rgb_input(self):
        # RGBA in, RGB565 out (alpha discarded).
        img = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
        self.assertEqual(rgb888_to_rgb565(img), b"\xff\xff")

    def test_matches_reference_for_random_pattern(self):
        # 8 specific pixels, packed independently with the reference packer.
        pixels = [
            (0, 0, 0),
            (255, 255, 255),
            (10, 20, 30),
            (200, 100, 50),
            (1, 2, 3),
            (123, 234, 89),
            (255, 0, 255),
            (0, 255, 255),
        ]
        img = Image.new("RGB", (len(pixels), 1))
        for i, p in enumerate(pixels):
            img.putpixel((i, 0), p)
        expected = b"".join(_pack565(*p) for p in pixels)
        self.assertEqual(rgb888_to_rgb565(img), expected)


if __name__ == "__main__":
    unittest.main()
