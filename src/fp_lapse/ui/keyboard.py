"""On-screen virtual keyboard (wifi-manual-config feature, PRD §3).

The project's first on-screen keyboard. Modelled on
`picker_datetime.py`: a frozen `KeyboardState`, a pure `render_keyboard`,
and a `KeyboardInteraction` whose `on_press` moves a grid cursor and
returns a `KeyboardAction` (or `None` while editing).

Layout: an alphabetical **ragged grid** — three char rows of 10
single-width keys + one bottom row of wide special keys (5 for the
`password` target, 4 for `ssid`). A LAYER key cycles
`abc → ABC → 123 → #+=`; together the four layers reach every printable
ASCII char (0x20–0x7E). The keyboard carries a `target` discriminator
(`"ssid"` | `"password"`) the App reads on commit; it is built
generically but is wired only to the Wi-Fi fields in this feature.

D-pad semantics: LEFT/RIGHT wrap within a row; UP/DOWN clamp at the
top/bottom and preserve horizontal position by proportion across the
char↔special-row boundary; OK actuates the key under the cursor; BACK
cancels the whole entry (it never deletes a char — that is `⌫`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from PIL import Image

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme, widgets

_BODY_PT: int = 11

# Length caps (PRD §3): WPA2-PSK passwords are 8–63 printable ASCII;
# SSIDs are 1–32 bytes.
PASSWORD_MIN: int = 8
PASSWORD_MAX: int = 63
SSID_MIN: int = 1
SSID_MAX: int = 32   # bytes (UTF-8)


# ----------------------------------------------------------------------
# Keys + layers
# ----------------------------------------------------------------------


class KeyKind(str, Enum):
    CHAR = "char"        # value is the literal char to type
    LAYER = "layer"      # cycle abc → ABC → 123 → #+=
    SPACE = "space"      # type " "
    BACKSPACE = "back"   # delete last char
    MASK = "mask"        # toggle show/hide (password only)
    DONE = "done"        # commit


@dataclass(frozen=True)
class Key:
    kind: KeyKind
    label: str           # what is drawn
    value: str = ""      # for CHAR/SPACE: the char inserted


# The four character layers — each 3 rows × 10 cols (30 cells). Together
# they cover all 95 printable ASCII: 52 letters, 10 digits, space (the
# `␣` special key), and all 32 punctuation marks.
_LAYERS = {
    "abc": (
        "abcdefghij",
        "klmnopqrst",
        "uvwxyz.-_@",
    ),
    "ABC": (
        "ABCDEFGHIJ",
        "KLMNOPQRST",
        "UVWXYZ.-_@",
    ),
    "123": (
        "1234567890",
        "!@#$%&*()-",
        "_+=/\\:;,.?",
    ),
    "#+=": (
        "\"'`~^|<>[]",
        "{}()-_+=/\\",
        ":;,.!?@#&*",
    ),
}

_LAYER_ORDER = ("abc", "ABC", "123", "#+=")
# Label of the LAYER key reflects the NEXT layer it will switch to.
_NEXT_LAYER = {
    cur: _LAYER_ORDER[(i + 1) % len(_LAYER_ORDER)]
    for i, cur in enumerate(_LAYER_ORDER)
}


def _char_rows(layer: str) -> List[Tuple[Key, ...]]:
    return [
        tuple(Key(KeyKind.CHAR, ch, ch) for ch in line)
        for line in _LAYERS[layer]
    ]


def _special_row(target: str, layer: str) -> Tuple[Key, ...]:
    keys = [
        Key(KeyKind.LAYER, _NEXT_LAYER[layer]),
        Key(KeyKind.SPACE, "␣", " "),
        Key(KeyKind.BACKSPACE, "⌫"),
    ]
    if target == "password":
        keys.append(Key(KeyKind.MASK, "◉"))
    keys.append(Key(KeyKind.DONE, "✓"))
    return tuple(keys)


def keyboard_rows(target: str, layer: str) -> Tuple[Tuple[Key, ...], ...]:
    """The ragged grid for `(target, layer)`: 3 char rows + 1 special row."""
    return tuple(_char_rows(layer) + [_special_row(target, layer)])


# ----------------------------------------------------------------------
# State + actions
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class KeyboardState:
    target: str                  # "ssid" | "password"
    text: str                    # current entered text
    layer: str                   # "abc" | "ABC" | "123" | "#+="
    masked: bool                 # password show/hide (ignored for ssid)
    cursor_row: int              # 0..3
    cursor_col: int              # 0..(row length - 1)
    error: Optional[str] = None  # inline validation message


class KeyboardAction(str, Enum):
    DONE = "done"        # ✓ pressed with valid text
    CANCEL = "cancel"    # BACK


def _remap_col(col: int, cur_len: int, new_len: int) -> int:
    """Proportionally remap a column when crossing rows of different len."""
    return min(col * new_len // cur_len, new_len - 1)


class KeyboardInteraction:
    """Cursor navigation + button-to-action translation for the keyboard."""

    def __init__(self, *, target: str, initial: str = "") -> None:
        if target not in ("ssid", "password"):
            raise ValueError(f"unknown keyboard target: {target!r}")
        self._target = target
        self._text = initial
        self._layer = "abc"
        self._masked = target == "password"
        self._cursor_row = 0
        self._cursor_col = 0
        self._error: Optional[str] = None

    # -- read-only properties -------------------------------------------
    @property
    def target(self) -> str:
        return self._target

    @property
    def text(self) -> str:
        return self._text

    @property
    def state(self) -> KeyboardState:
        return KeyboardState(
            target=self._target,
            text=self._text,
            layer=self._layer,
            masked=self._masked,
            cursor_row=self._cursor_row,
            cursor_col=self._cursor_col,
            error=self._error,
        )

    # -- interaction ----------------------------------------------------
    def on_press(self, button: ButtonId) -> Optional[KeyboardAction]:
        rows = keyboard_rows(self._target, self._layer)
        row_len = len(rows[self._cursor_row])
        if button == ButtonId.LEFT:
            self._cursor_col = (self._cursor_col - 1) % row_len
            return None
        if button == ButtonId.RIGHT:
            self._cursor_col = (self._cursor_col + 1) % row_len
            return None
        if button == ButtonId.UP:
            if self._cursor_row > 0:
                new_row = self._cursor_row - 1
                self._cursor_col = _remap_col(
                    self._cursor_col, row_len, len(rows[new_row]),
                )
                self._cursor_row = new_row
            return None
        if button == ButtonId.DOWN:
            if self._cursor_row < len(rows) - 1:
                new_row = self._cursor_row + 1
                self._cursor_col = _remap_col(
                    self._cursor_col, row_len, len(rows[new_row]),
                )
                self._cursor_row = new_row
            return None
        if button == ButtonId.OK:
            return self._actuate(rows[self._cursor_row][self._cursor_col])
        if button == ButtonId.BACK:
            return KeyboardAction.CANCEL
        return None

    def _actuate(self, key: Key) -> Optional[KeyboardAction]:
        if key.kind in (KeyKind.CHAR, KeyKind.SPACE):
            self._type(key.value)
            return None
        if key.kind == KeyKind.BACKSPACE:
            self._text = self._text[:-1]
            self._error = None
            return None
        if key.kind == KeyKind.LAYER:
            self._layer = _NEXT_LAYER[self._layer]
            self._error = None
            # Cursor stays put: char rows are all length 10 and the
            # special row length is unchanged by a layer switch.
            return None
        if key.kind == KeyKind.MASK:
            if self._target == "password":
                self._masked = not self._masked
            return None
        if key.kind == KeyKind.DONE:
            return self._try_done()
        return None

    def _type(self, ch: str) -> None:
        if self._fits(self._text + ch):
            self._text += ch
            self._error = None
        else:
            self._error = (
                f"Max {PASSWORD_MAX} chars" if self._target == "password"
                else f"Max {SSID_MAX} chars"
            )

    def _fits(self, candidate: str) -> bool:
        if self._target == "password":
            return len(candidate) <= PASSWORD_MAX
        return len(candidate.encode("utf-8")) <= SSID_MAX

    def _try_done(self) -> Optional[KeyboardAction]:
        if self._target == "password":
            if PASSWORD_MIN <= len(self._text) <= PASSWORD_MAX:
                self._error = None
                return KeyboardAction.DONE
            self._error = f"{PASSWORD_MIN}–{PASSWORD_MAX} chars"
            return None
        nbytes = len(self._text.encode("utf-8"))
        if SSID_MIN <= nbytes <= SSID_MAX:
            self._error = None
            return KeyboardAction.DONE
        self._error = f"{SSID_MIN}–{SSID_MAX} chars"
        return None


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

_TITLE_Y: int = 4
_TITLE_LINE_Y: int = 20
_ENTRY_Y: int = 26
_ENTRY_BOX = (8, 24, 312, 42)     # x0, y0, x1, y1
_ERROR_Y: int = 44
_GRID_TOP: int = 54
_ROW_STRIDE: int = 38             # rows at y = 54, 92, 130, 168
_CELL_H: int = 30
_CHAR_COL_X0: int = 10
_CHAR_COL_W: int = 30             # 10 cols × 30 = 300
_SPECIAL_USABLE_W: int = 300


def _draw_key(draw, font, key: Key, x0: int, y0: int, w: int, selected: bool) -> None:
    if selected:
        draw.rectangle([x0, y0, x0 + w - 1, y0 + _CELL_H - 1], fill=theme.SEL_BG)
        draw.rectangle([x0, y0, x0 + 2, y0 + _CELL_H - 1], fill=theme.SEL_BAR)
        color = theme.SEL_FG
    else:
        color = theme.FG
    label = key.label
    tw = int(draw.textlength(label, font=font))
    tx = x0 + (w - tw) // 2
    ty = y0 + (_CELL_H - 14) // 2
    draw.text((tx, ty), label, font=font, fill=color)


def render_keyboard(
    base: Image.Image, state: KeyboardState, *, title: str,
) -> Image.Image:
    """Compose the keyboard over `base`. Returns a fresh RGB 320x240 image."""
    rgba, draw = widgets.new_overlay_canvas(base)
    font = fonts.mono(_BODY_PT)

    # Title + separator.
    draw.text((4, _TITLE_Y), title, font=font, fill=theme.FG)
    draw.line([(0, _TITLE_LINE_Y), (WIDTH, _TITLE_LINE_Y)], fill=theme.SEP)

    # Entry box + the typed text (masked for password).
    draw.rectangle(list(_ENTRY_BOX), outline=theme.DIALOG_BORDER)
    if state.target == "password" and state.masked:
        shown = "•" * len(state.text)
    else:
        shown = state.text
    draw.text((12, _ENTRY_Y), shown + "▏", font=font, fill=theme.FG)

    # Inline validation error.
    if state.error:
        draw.text((12, _ERROR_Y), state.error, font=font, fill=theme.ERR)

    rows = keyboard_rows(state.target, state.layer)
    for r, row in enumerate(rows):
        y0 = _GRID_TOP + r * _ROW_STRIDE
        if r < 3:
            for c, key in enumerate(row):
                x0 = _CHAR_COL_X0 + c * _CHAR_COL_W
                _draw_key(
                    draw, font, key, x0, y0, _CHAR_COL_W,
                    selected=(r == state.cursor_row and c == state.cursor_col),
                )
        else:
            cell_w = _SPECIAL_USABLE_W // len(row)
            for c, key in enumerate(row):
                x0 = _CHAR_COL_X0 + c * cell_w
                _draw_key(
                    draw, font, key, x0, y0, cell_w,
                    selected=(r == state.cursor_row and c == state.cursor_col),
                )

    widgets.footer(draw, "↑↓←→ move  OK type  ESC cancel")
    return rgba.convert("RGB")
