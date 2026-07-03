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

from datetime import date as date_t, time as time_t  # noqa: E402

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH  # noqa: E402
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402
from fp_lapse.ui.edit_screen import (  # noqa: E402
    EditAction,
    EditScreen,
    EditScreenInteraction,
    EditState,
    editable_fields,
)


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

    def test_generate_bracket_row_after_end(self):
        # semiauto-bracketing §4: the generator entry row sits at index 5,
        # after the schedule pair (3=start, 4=end) and before the per-shot
        # rows. Its value cell is a hint, not a cyclable value.
        f = editable_fields(TOTALITY)
        self.assertEqual(f[5], ("generate bracket", "→ open"))

    def test_shot_fields_in_order(self):
        # semiauto-bracketing §4: the generate-bracket row at index 5
        # pushes the per-shot rows down by one. Shot 1 shutter is now at
        # index 6 (was 5).
        f = editable_fields(TOTALITY)
        self.assertEqual(f[6], ("#1 shutter", "1/500"))
        self.assertEqual(f[7], ("#1 iso", "400"))
        self.assertEqual(f[8], ("#1 aperture", "—"))
        # Shot 5: shutter=2s, iso=1600, aperture=None.
        self.assertEqual(f[18], ("#5 shutter", "2 s"))
        self.assertEqual(f[19], ("#5 iso", "1600"))
        self.assertEqual(f[20], ("#5 aperture", "—"))

    def test_total_count(self):
        # 6 header fields (name, interval, shots, start, end, generate
        # bracket) + 3 per shot × 5 shots = 21.
        self.assertEqual(len(editable_fields(TOTALITY)), 21)

    def test_null_aperture_renders_dash(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),)
        )
        f = editable_fields(cfg)
        self.assertEqual(f[8], ("#1 aperture", "—"))

    def test_concrete_aperture_renders_plain_number(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=5.6),)
        )
        f = editable_fields(cfg)
        self.assertEqual(f[8], ("#1 aperture", "5.6"))


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


class TestStartEndFields(unittest.TestCase):
    """prd2.md §6.2 — START/END fields between `shots` and the shot rows."""

    def test_start_and_end_at_index_3_and_4(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
            start=ScheduledMoment(time=time_t(9, 0, 0), date=None),
            end=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
        )
        f = editable_fields(cfg)
        self.assertEqual(f[3][0], "start")
        self.assertEqual(f[4][0], "end")

    def test_none_renders_em_dash(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        f = editable_fields(cfg)
        self.assertEqual(f[3], ("start", "—"))
        self.assertEqual(f[4], ("end", "—"))

    def test_time_only_renders_hhmmss(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
            start=ScheduledMoment(time=time_t(9, 0, 0), date=None),
        )
        f = editable_fields(cfg)
        self.assertEqual(f[3], ("start", "09:00:00"))

    def test_date_time_renders_iso(self):
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
            start=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
        )
        f = editable_fields(cfg)
        self.assertEqual(f[3], ("start", "2026-08-12 11:33:23"))

    def test_auto_mode_still_has_start_end(self):
        cfg = TimelapseConfig("Auto", 30.0, ())
        f = editable_fields(cfg)
        # 0=name, 1=interval, 2=shots, 3=start, 4=end, 5=generate, no
        # shot rows. The generate-bracket row is always present.
        self.assertEqual(len(f), 6)
        self.assertEqual(f[3][0], "start")
        self.assertEqual(f[4][0], "end")
        self.assertEqual(f[5], ("generate bracket", "→ open"))


class TestStartEndInteraction(unittest.TestCase):
    """Addendum F — LEFT/RIGHT on START/END open the picker; OK is
    uniformly SAVE. Mode switching + clearing live in the picker now."""

    def _at_start(self) -> EditScreenInteraction:
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        ix = EditScreenInteraction(cfg)
        ix.field_cursor = 3  # cursor on start
        return ix

    def test_right_on_start_opens_picker(self):
        ix = self._at_start()
        self.assertEqual(
            ix.on_press(ButtonId.RIGHT),
            EditAction.OPEN_PICKER_START,
        )
        # The draft is unchanged — the picker is responsible for any
        # mutation, applied later in the App's dispatch.
        self.assertIsNone(ix.draft.start)

    def test_left_on_start_also_opens_picker(self):
        """Both LEFT and RIGHT open the picker on START/END — there's
        nothing meaningful to cycle for a datetime field."""
        ix = self._at_start()
        self.assertEqual(
            ix.on_press(ButtonId.LEFT),
            EditAction.OPEN_PICKER_START,
        )

    def test_right_on_end_opens_picker(self):
        ix = self._at_start()
        ix.field_cursor = 4  # cursor on end
        self.assertEqual(
            ix.on_press(ButtonId.RIGHT),
            EditAction.OPEN_PICKER_END,
        )

    def test_left_right_on_other_fields_still_cycles(self):
        """The picker-on-LEFT/RIGHT only kicks in for START/END;
        every other field keeps the in-place value cycler."""
        ix = self._at_start()
        ix.field_cursor = 1  # cursor on interval
        before = ix.draft.interval_s
        # Cycler returns None (it acts in-place on the draft).
        self.assertIsNone(ix.on_press(ButtonId.RIGHT))
        self.assertNotEqual(ix.draft.interval_s, before)

    def test_ok_on_start_returns_save(self):
        """Addendum F: OK uniformly means SAVE on every field."""
        ix = self._at_start()
        self.assertEqual(ix.on_press(ButtonId.OK), EditAction.SAVE)

    def test_ok_on_end_returns_save(self):
        ix = self._at_start()
        ix.field_cursor = 4
        self.assertEqual(ix.on_press(ButtonId.OK), EditAction.SAVE)

    def test_ok_on_name_returns_save(self):
        ix = self._at_start()
        ix.field_cursor = 0
        self.assertEqual(ix.on_press(ButtonId.OK), EditAction.SAVE)


class TestGenerateBracketRowInteraction(unittest.TestCase):
    """semiauto-bracketing §4 — LEFT/RIGHT on the generate-bracket row
    open the generator; OK stays = SAVE."""

    def _ix(self) -> EditScreenInteraction:
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        ix = EditScreenInteraction(cfg)
        ix.field_cursor = 5   # the generate-bracket row
        return ix

    def test_right_opens_generator(self):
        ix = self._ix()
        self.assertEqual(ix.on_press(ButtonId.RIGHT), EditAction.OPEN_GENERATOR)
        # The draft is untouched — the generator does any mutation later.
        self.assertEqual(len(ix.draft.shots), 1)
        self.assertFalse(ix.is_dirty)

    def test_left_also_opens_generator(self):
        ix = self._ix()
        self.assertEqual(ix.on_press(ButtonId.LEFT), EditAction.OPEN_GENERATOR)

    def test_ok_on_generate_row_returns_save(self):
        ix = self._ix()
        self.assertEqual(ix.on_press(ButtonId.OK), EditAction.SAVE)

    def test_generate_row_present_in_auto_mode(self):
        cfg = TimelapseConfig("Auto", 30.0, ())
        ix = EditScreenInteraction(cfg)
        ix.field_cursor = 5
        self.assertEqual(ix.on_press(ButtonId.RIGHT), EditAction.OPEN_GENERATOR)


class TestNameRowInteraction(unittest.TestCase):
    """The `name` row's LEFT/RIGHT open the on-screen keyboard."""

    def _ix(self) -> EditScreenInteraction:
        cfg = TimelapseConfig(
            "X", 10.0, (Shot(shutter=1 / 500, iso=200, aperture=None),),
        )
        ix = EditScreenInteraction(cfg)
        ix.field_cursor = 0   # the name row
        return ix

    def test_right_opens_name_keyboard(self):
        ix = self._ix()
        self.assertEqual(
            ix.on_press(ButtonId.RIGHT), EditAction.OPEN_NAME_KEYBOARD,
        )
        self.assertFalse(ix.is_dirty)

    def test_left_opens_name_keyboard(self):
        ix = self._ix()
        self.assertEqual(
            ix.on_press(ButtonId.LEFT), EditAction.OPEN_NAME_KEYBOARD,
        )


class TestEditScreenWithScheduleVisualRegression(unittest.TestCase):
    """Pixel-exact match against `docs/mockups/11_edit_with_schedule.png`."""

    def test_11_edit_with_schedule(self):
        cfg = TimelapseConfig(
            name="Totality", interval_s=5.0,
            shots=(
                Shot(shutter=1 / 500, iso=400, aperture=None),
                Shot(shutter=1 / 125, iso=400, aperture=None),
                Shot(shutter=1 / 30, iso=400, aperture=None),
                Shot(shutter=1 / 8, iso=400, aperture=None),
                Shot(shutter=2.0, iso=1600, aperture=None),
            ),
            start=ScheduledMoment(
                time=time_t(11, 33, 23), date=date_t(2026, 8, 12),
            ),
            end=ScheduledMoment(
                time=time_t(11, 36, 9), date=date_t(2026, 8, 12),
            ),
        )
        actual = EditScreen().render(
            EditState(cfg=cfg, field_cursor=3, scroll_offset=0)
        )
        expected_path = MOCKUPS_DIR / "11_edit_with_schedule.png"
        self.assertTrue(expected_path.exists())
        expected = Image.open(expected_path).convert("RGB")
        if actual.tobytes() != expected.tobytes():
            out = _dump_artifacts("11_edit_with_schedule", expected, actual)
            self.fail(
                f"11_edit_with_schedule.png differs — see {out}/11_*.png"
            )


if __name__ == "__main__":
    unittest.main()
