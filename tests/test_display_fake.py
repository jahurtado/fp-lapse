"""Tests for `InMemoryDisplay`."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from PIL import Image  # noqa: E402

from fp_lapse.display import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.display.fake import InMemoryDisplay  # noqa: E402


class TestInMemoryDisplay(unittest.TestCase):
    def test_starts_empty(self):
        d = InMemoryDisplay()
        self.assertIsNone(d.last_frame)
        self.assertEqual(d.frame_count, 0)

    def test_blit_stores_copy_and_counts(self):
        d = InMemoryDisplay()
        img = new_canvas((10, 20, 30))
        d.blit(img)
        self.assertIsNotNone(d.last_frame)
        self.assertEqual(d.frame_count, 1)
        self.assertEqual(d.last_frame.size, (WIDTH, HEIGHT))

        # Mutate caller's image; stored copy must not change.
        from PIL import ImageDraw
        ImageDraw.Draw(img).rectangle((0, 0, 100, 100), fill=(255, 255, 255))
        self.assertEqual(d.last_frame.getpixel((50, 50)), (10, 20, 30))

    def test_blit_wrong_size_raises(self):
        d = InMemoryDisplay()
        with self.assertRaises(ValueError):
            d.blit(Image.new("RGB", (100, 100), (0, 0, 0)))

    def test_blit_after_close_raises(self):
        d = InMemoryDisplay()
        d.close()
        with self.assertRaises(RuntimeError):
            d.blit(new_canvas())

    def test_save_last_frame_writes_png(self):
        d = InMemoryDisplay()
        d.blit(new_canvas((255, 0, 0)))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "frame.png"
            returned = d.save_last_frame(out)
            self.assertEqual(returned, out)
            self.assertTrue(out.exists())
            with Image.open(out) as loaded:
                self.assertEqual(loaded.size, (WIDTH, HEIGHT))
                # PNG round-trip preserves pixels.
                self.assertEqual(loaded.convert("RGB").getpixel((10, 10)),
                                 (255, 0, 0))

    def test_save_last_frame_scale(self):
        d = InMemoryDisplay()
        d.blit(new_canvas())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "frame.png"
            d.save_last_frame(out, scale=3)
            with Image.open(out) as loaded:
                self.assertEqual(loaded.size, (WIDTH * 3, HEIGHT * 3))

    def test_save_with_no_frame_raises(self):
        d = InMemoryDisplay()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                d.save_last_frame(Path(td) / "x.png")


if __name__ == "__main__":
    unittest.main()
