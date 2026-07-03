"""Tests for the on-screen virtual keyboard (wifi-manual-config §3)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    KeyboardAction,
    KeyboardInteraction,
    KeyboardState,
    render_keyboard,
)
from fp_lapse.ui.keyboard import (  # noqa: E402
    KeyKind,
    _LAYER_ORDER,
    keyboard_rows,
)


def _type_chars(ix: KeyboardInteraction, s: str) -> None:
    """Helper: drive the keyboard to type each char of `s`.

    Switches layers as needed and walks the grid to the target key.
    """
    for ch in s:
        _type_one(ix, ch)


def _find_key(target: str, ch: str):
    """Return (layer, row, col) for a CHAR/SPACE key producing `ch`."""
    for layer in _LAYER_ORDER:
        rows = keyboard_rows(target, layer)
        for r, row in enumerate(rows):
            for c, key in enumerate(row):
                if key.kind in (KeyKind.CHAR, KeyKind.SPACE) and key.value == ch:
                    return layer, r, c
    raise AssertionError(f"no key for {ch!r}")


def _type_one(ix: KeyboardInteraction, ch: str) -> None:
    layer, r, c = _find_key(ix.target, ch)
    # Cycle to the right layer via the LAYER key (special row last item
    # area). Simpler: set layer by pressing LAYER until matched.
    guard = 0
    while ix.state.layer != layer and guard < 8:
        _press_layer(ix)
        guard += 1
    # Navigate to (r, c).
    _goto(ix, r, c)
    ix.on_press(ButtonId.OK)


def _press_layer(ix: KeyboardInteraction) -> None:
    rows = keyboard_rows(ix.target, ix.state.layer)
    special = rows[-1]
    layer_col = next(i for i, k in enumerate(special) if k.kind == KeyKind.LAYER)
    _goto(ix, len(rows) - 1, layer_col)
    ix.on_press(ButtonId.OK)


def _goto(ix: KeyboardInteraction, r: int, c: int) -> None:
    # Move to row 0 first, then down to r; reset col by pressing LEFT a
    # full row width then RIGHT c times.
    while ix.state.cursor_row > 0:
        ix.on_press(ButtonId.UP)
    while ix.state.cursor_row < r:
        ix.on_press(ButtonId.DOWN)
    # Normalise column: press LEFT 12 times (≥ any row length) to wrap to 0.
    for _ in range(12):
        if ix.state.cursor_col == 0:
            break
        ix.on_press(ButtonId.LEFT)
    for _ in range(c):
        ix.on_press(ButtonId.RIGHT)


class TestNavigation(unittest.TestCase):
    def test_left_right_wrap_within_row(self):
        ix = KeyboardInteraction(target="password")
        self.assertEqual(ix.state.cursor_col, 0)
        ix.on_press(ButtonId.LEFT)
        self.assertEqual(ix.state.cursor_col, 9)  # wrapped to last char col
        ix.on_press(ButtonId.RIGHT)
        self.assertEqual(ix.state.cursor_col, 0)

    def test_up_clamps_at_top(self):
        ix = KeyboardInteraction(target="ssid")
        ix.on_press(ButtonId.UP)
        self.assertEqual(ix.state.cursor_row, 0)

    def test_down_clamps_at_bottom(self):
        ix = KeyboardInteraction(target="ssid")
        for _ in range(8):
            ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.state.cursor_row, 3)

    def test_char_to_special_preserves_horizontal_proportion(self):
        ix = KeyboardInteraction(target="password")  # 5 special keys
        _goto(ix, 2, 8)   # char row, col 8 of 10
        ix.on_press(ButtonId.DOWN)
        self.assertEqual(ix.state.cursor_row, 3)
        # 8 * 5 // 10 = 4
        self.assertEqual(ix.state.cursor_col, 4)

    def test_special_to_char_preserves_horizontal_proportion(self):
        ix = KeyboardInteraction(target="password")
        _goto(ix, 3, 4)   # last special key
        ix.on_press(ButtonId.UP)
        self.assertEqual(ix.state.cursor_row, 2)
        # 4 * 10 // 5 = 8
        self.assertEqual(ix.state.cursor_col, 8)


class TestTyping(unittest.TestCase):
    def test_type_char(self):
        ix = KeyboardInteraction(target="password")
        _goto(ix, 0, 0)  # 'a'
        ix.on_press(ButtonId.OK)
        self.assertEqual(ix.text, "a")

    def test_space_key_types_space(self):
        ix = KeyboardInteraction(target="ssid")
        _type_one(ix, " ")
        self.assertEqual(ix.text, " ")

    def test_backspace_deletes_last(self):
        ix = KeyboardInteraction(target="ssid")
        _type_chars(ix, "ab")
        self.assertEqual(ix.text, "ab")
        # press backspace
        rows = keyboard_rows(ix.target, ix.state.layer)
        bs_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.BACKSPACE)
        _goto(ix, 3, bs_col)
        ix.on_press(ButtonId.OK)
        self.assertEqual(ix.text, "a")

    def test_backspace_noop_on_empty(self):
        ix = KeyboardInteraction(target="ssid")
        rows = keyboard_rows(ix.target, ix.state.layer)
        bs_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.BACKSPACE)
        _goto(ix, 3, bs_col)
        ix.on_press(ButtonId.OK)
        self.assertEqual(ix.text, "")


class TestLayers(unittest.TestCase):
    def test_layer_cycle(self):
        ix = KeyboardInteraction(target="password")
        self.assertEqual(ix.state.layer, "abc")
        for expected in ("ABC", "123", "#+=", "abc"):
            _press_layer(ix)
            self.assertEqual(ix.state.layer, expected)

    def test_full_ascii_reachable(self):
        """Every printable ASCII char 0x20–0x7E is reachable on some key."""
        reachable = set()
        # Space is the ␣ special key.
        reachable.add(" ")
        for layer in _LAYER_ORDER:
            for row in keyboard_rows("password", layer):
                for key in row:
                    if key.kind in (KeyKind.CHAR, KeyKind.SPACE):
                        reachable.add(key.value)
        required = {chr(c) for c in range(0x20, 0x7F)}
        missing = required - reachable
        self.assertEqual(missing, set(), f"unreachable chars: {sorted(missing)}")


class TestMask(unittest.TestCase):
    def test_password_starts_masked(self):
        ix = KeyboardInteraction(target="password")
        self.assertTrue(ix.state.masked)

    def test_mask_toggle(self):
        ix = KeyboardInteraction(target="password")
        rows = keyboard_rows(ix.target, ix.state.layer)
        mask_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.MASK)
        _goto(ix, 3, mask_col)
        ix.on_press(ButtonId.OK)
        self.assertFalse(ix.state.masked)
        ix.on_press(ButtonId.OK)
        self.assertTrue(ix.state.masked)

    def test_mask_does_not_alter_text(self):
        ix = KeyboardInteraction(target="password")
        _type_chars(ix, "secret12")
        before = ix.text
        rows = keyboard_rows(ix.target, ix.state.layer)
        mask_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.MASK)
        _goto(ix, 3, mask_col)
        ix.on_press(ButtonId.OK)
        self.assertEqual(ix.text, before)

    def test_ssid_has_no_mask_key(self):
        rows = keyboard_rows("ssid", "abc")
        kinds = [k.kind for k in rows[-1]]
        self.assertNotIn(KeyKind.MASK, kinds)
        self.assertEqual(len(rows[-1]), 4)

    def test_password_has_mask_key(self):
        rows = keyboard_rows("password", "abc")
        kinds = [k.kind for k in rows[-1]]
        self.assertIn(KeyKind.MASK, kinds)
        self.assertEqual(len(rows[-1]), 5)


class TestDoneValidation(unittest.TestCase):
    def _press_done(self, ix):
        rows = keyboard_rows(ix.target, ix.state.layer)
        done_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.DONE)
        _goto(ix, 3, done_col)
        return ix.on_press(ButtonId.OK)

    def test_password_valid_length_returns_done(self):
        ix = KeyboardInteraction(target="password", initial="abcdefgh")  # 8
        self.assertIs(self._press_done(ix), KeyboardAction.DONE)

    def test_password_too_short_stays_open_with_error(self):
        ix = KeyboardInteraction(target="password", initial="abcdefg")  # 7
        self.assertIsNone(self._press_done(ix))
        self.assertIsNotNone(ix.state.error)

    def test_password_too_long_stays_open(self):
        ix = KeyboardInteraction(target="password", initial="x" * 64)
        self.assertIsNone(self._press_done(ix))
        self.assertIsNotNone(ix.state.error)

    def test_ssid_valid_returns_done(self):
        ix = KeyboardInteraction(target="ssid", initial="Net")
        self.assertIs(self._press_done(ix), KeyboardAction.DONE)

    def test_ssid_empty_stays_open_with_error(self):
        ix = KeyboardInteraction(target="ssid", initial="")
        self.assertIsNone(self._press_done(ix))
        self.assertIsNotNone(ix.state.error)


class TestLengthCaps(unittest.TestCase):
    def test_password_cap_ignored_past_max(self):
        ix = KeyboardInteraction(target="password", initial="x" * PASSWORD_MAX_VALUE())
        _goto(ix, 0, 0)
        ix.on_press(ButtonId.OK)  # would be the 64th char
        self.assertEqual(len(ix.text), PASSWORD_MAX_VALUE())

    def test_ssid_cap_33_bytes_unreachable(self):
        ix = KeyboardInteraction(target="ssid", initial="x" * 32)
        _goto(ix, 0, 0)
        ix.on_press(ButtonId.OK)
        self.assertEqual(len(ix.text), 32)


class TestCancel(unittest.TestCase):
    def test_back_returns_cancel(self):
        ix = KeyboardInteraction(target="password", initial="secret12")
        self.assertIs(ix.on_press(ButtonId.BACK), KeyboardAction.CANCEL)

    def test_back_does_not_delete_char(self):
        ix = KeyboardInteraction(target="password", initial="secret12")
        ix.on_press(ButtonId.BACK)
        self.assertEqual(ix.text, "secret12")


class TestConfigNameTarget(unittest.TestCase):
    """Config-name editing target (semiauto-bracketing addendum)."""

    def _press_done(self, ix):
        rows = keyboard_rows(ix.target, ix.state.layer)
        done_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.DONE)
        _goto(ix, 3, done_col)
        return ix.on_press(ButtonId.OK)

    def test_config_name_target_accepted(self):
        ix = KeyboardInteraction(target="config_name", initial="Hello")
        self.assertEqual(ix.target, "config_name")
        self.assertEqual(ix.text, "Hello")
        self.assertFalse(ix.state.masked)

    def test_config_name_special_row_has_no_mask_key(self):
        rows = keyboard_rows("config_name", "abc")
        kinds = [k.kind for k in rows[-1]]
        self.assertNotIn(KeyKind.MASK, kinds)
        self.assertEqual(len(rows[-1]), 4)

    def test_overflow_past_20_sets_error_and_is_rejected(self):
        ix = KeyboardInteraction(target="config_name", initial="x" * 20)
        _goto(ix, 0, 0)
        ix.on_press(ButtonId.OK)  # would be the 21st char
        self.assertEqual(len(ix.text), 20)
        self.assertEqual(ix.state.error, "Max 20 chars")

    def test_done_on_empty_errors_no_done(self):
        ix = KeyboardInteraction(target="config_name", initial="")
        self.assertIsNone(self._press_done(ix))
        self.assertEqual(ix.state.error, "1–20 chars")

    def test_done_on_taken_name_errors_no_done(self):
        ix = KeyboardInteraction(
            target="config_name", initial="Dup",
            taken_names=frozenset({"Dup"}),
        )
        self.assertIsNone(self._press_done(ix))
        self.assertEqual(ix.state.error, "Name in use")

    def test_done_on_valid_unique_name_returns_done(self):
        ix = KeyboardInteraction(
            target="config_name", initial="Totality",
            taken_names=frozenset({"Other"}),
        )
        self.assertIs(self._press_done(ix), KeyboardAction.DONE)


class TestRender(unittest.TestCase):
    def test_renders_320x240(self):
        ix = KeyboardInteraction(target="password")
        out = render_keyboard(new_canvas(), ix.state, title="Wi-Fi password")
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_masked_vs_shown_differ(self):
        ix = KeyboardInteraction(target="password", initial="secret12")
        masked = render_keyboard(new_canvas(), ix.state, title="Wi-Fi password").tobytes()
        shown_state = KeyboardState(
            target="password", text="secret12", layer="abc", masked=False,
            cursor_row=0, cursor_col=0,
        )
        shown = render_keyboard(new_canvas(), shown_state, title="Wi-Fi password").tobytes()
        self.assertNotEqual(masked, shown)


MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"


class TestKeyboardVisualRegression(unittest.TestCase):
    def _assert(self, state: KeyboardState, title: str, name: str) -> None:
        path = MOCKUPS_DIR / f"{name}.png"
        self.assertTrue(path.exists(), f"missing mockup: {path}")
        expected = Image.open(path).convert("RGB")
        actual = render_keyboard(new_canvas(), state, title=title)
        self.assertEqual(actual.tobytes(), expected.tobytes(),
                         f"{name}.png differs from production render")

    def test_22_keyboard_password_abc(self):
        self._assert(
            KeyboardState(
                target="password", text="hunter7", layer="abc", masked=True,
                cursor_row=3, cursor_col=3,
            ),
            "Wi-Fi password", "22_keyboard_password_abc",
        )

    def test_23_keyboard_ssid(self):
        self._assert(
            KeyboardState(
                target="ssid", text="Hidden", layer="abc", masked=False,
                cursor_row=0, cursor_col=0,
            ),
            "Network name", "23_keyboard_ssid",
        )


def PASSWORD_MAX_VALUE() -> int:
    from fp_lapse.ui.keyboard import PASSWORD_MAX
    return PASSWORD_MAX


if __name__ == "__main__":
    unittest.main()
