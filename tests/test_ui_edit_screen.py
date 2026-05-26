"""Tests for the edit screen (`EditScreen`).

Smoke + pixel-exact visual regression against `docs/mockups/04_edit.png`.
Same strategy as `test_ui_main_screen.py`: when a regression fails,
both PNGs (expected + actual + diff in magenta) are dumped to
`runtime/test_artifacts/`.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.ui.edit_screen import EditScreen, EditState, editable_fields  # noqa: E402


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "test_artifacts"


TOTALITY = TimelapseConfig(
    name="Totality", interval_s=5.0,
    shots=(
        Shot(shutter=1 / 500, iso=400, aperture=None),
        Shot(shutter=1 / 125, iso=400, aperture=None),
        Shot(shutter=1 / 30,  iso=400, aperture=None),
        Shot(shutter=1 / 8,   iso=400, aperture=None),
        Shot(shutter=2.0,     iso=1600, aperture=None),
    ),
)


def _dump_artifacts(name: str, expected: Image.Image, actual: Image.Image) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    expected.save(ARTIFACTS_DIR / f"{name}_expected.png")
    actual.save(ARTIFACTS_DIR / f"{name}_actual.png")
    diff = Image.new("RGB", expected.size, (0, 0, 0))
    ep = expected.convert("RGB").load()
    ap = actual.convert("RGB").load()
    dp = diff.load()
    for j in range(expected.size[1]):
        for i in range(expected.size[0]):
            if ep[i, j] != ap[i, j]:
                dp[i, j] = (255, 0, 255)
    diff.save(ARTIFACTS_DIR / f"{name}_diff.png")
    return ARTIFACTS_DIR


class TestEditableFields(unittest.TestCase):
    def test_header_fields_first(self):
        f = editable_fields(TOTALITY)
        self.assertEqual(f[0], ("name", "Totality"))
        self.assertEqual(f[1], ("interval", "5 s"))
        self.assertEqual(f[2], ("shots", "5"))

    def test_shot_fields_in_order(self):
        f = editable_fields(TOTALITY)
        # Shot 1: shutter=1/500, iso=400, aperture=None.
        self.assertEqual(f[3], ("#1 shutter", "1/500"))
        self.assertEqual(f[4], ("#1 iso", "400"))
        self.assertEqual(f[5], ("#1 aperture", "—"))
        # Shot 5: shutter=2s, iso=1600, aperture=None.
        self.assertEqual(f[15], ("#5 shutter", "2 s"))
        self.assertEqual(f[16], ("#5 iso", "1600"))
        self.assertEqual(f[17], ("#5 aperture", "—"))

    def test_total_count(self):
        # 3 header fields + 3 per shot × 5 shots = 18.
        self.assertEqual(len(editable_fields(TOTALITY)), 18)

    def test_null_aperture_renders_dash(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),)
        )
        f = editable_fields(cfg)
        self.assertEqual(f[5], ("#1 aperture", "—"))

    def test_concrete_aperture_renders_plain_number(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=5.6),)
        )
        f = editable_fields(cfg)
        self.assertEqual(f[5], ("#1 aperture", "5.6"))


class TestEditScreenSmoke(unittest.TestCase):
    def test_renders_320x240_rgb(self):
        state = EditState(cfg=TOTALITY, field_cursor=3, scroll_offset=0)
        img = EditScreen().render(state)
        self.assertEqual(img.size, (WIDTH, HEIGHT))
        self.assertEqual(img.mode, "RGB")

    def test_handles_minimal_config(self):
        cfg = TimelapseConfig(
            "Solo", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),)
        )
        state = EditState(cfg=cfg, field_cursor=0, scroll_offset=0)
        img = EditScreen().render(state)
        self.assertEqual(img.size, (WIDTH, HEIGHT))

    def test_cursor_on_first_field(self):
        # Should not crash; the band lands on the `name` row.
        state = EditState(cfg=TOTALITY, field_cursor=0, scroll_offset=0)
        EditScreen().render(state)

    def test_cursor_below_visible_area(self):
        # Cursor on last shot field of Totality (#5 aperture). With
        # scroll_offset=0 the cursor would be off-screen, but rendering
        # should not crash — the visible window just doesn't show it.
        state = EditState(
            cfg=TOTALITY, field_cursor=17, scroll_offset=0
        )
        img = EditScreen().render(state)
        self.assertEqual(img.size, (WIDTH, HEIGHT))


class TestEditScreenVisualRegression(unittest.TestCase):
    """Pixel-exact match contra `docs/mockups/04_edit.png` (Mac-only)."""

    def test_04_edit(self):
        state = EditState(cfg=TOTALITY, field_cursor=3, scroll_offset=0)
        actual = EditScreen().render(state)
        expected_path = MOCKUPS_DIR / "04_edit.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("04_edit", expected, actual)
            self.fail(
                f"04_edit.png differs from production render — "
                f"see {out}/04_edit_{{expected,actual,diff}}.png"
            )


if __name__ == "__main__":
    unittest.main()
