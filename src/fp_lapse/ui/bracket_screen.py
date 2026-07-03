"""Semiautomatic bracketing generator screen
(`docs/features/semiauto-bracketing` §3).

A pure-rendering secondary screen reached from the edit screen, following
the `*State` + `render_*` + `*Interaction` idiom of `edit_screen.py` /
`picker_datetime.py`. It collects the generator parameters (reference
exposure, direction, EV step, shot count, ISO1, ISO2) and shows a live
preview of the generated ladder, computed by calling the pure
`fp_lapse.bracket.generate_bracket` with the injected `SHUTTER_VALUES`
grid.

On accept the App reads `result().shots` and writes them into the edit
draft; on cancel the draft is left untouched. The generator itself does
no I/O and persists nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from ..bracket import BracketResult, BracketSpec, generate_bracket
from ..buttons.iface import ButtonId
from ..configs import Shot
from ..display.iface import HEIGHT, WIDTH, new_canvas
from . import fonts, theme, widgets
from .edit_values import (
    APERTURE_VALUES,
    DIRECTION_BRIGHTEST,
    DIRECTION_DARKEST,
    DIRECTION_VALUES,
    EV_STEP_VALUES,
    BRACKET_N_VALUES,
    ISO2_OFF,
    ISO2_VALUES,
    ISO_VALUES,
    SHUTTER_VALUES,
    cycle_in_list,
    format_direction,
    format_ev_step,
    format_iso2,
)

_BODY_PT: int = 11
_N_FIELDS: int = 8

_TITLE_Y: int = 2
_TITLE_LINE_Y: int = 18
_FIELDS_TOP_Y: int = 22
_ROW_HEIGHT: int = 13
# Preview block geometry — below the 8 field rows.
_SEP_GAP: int = 3
_PREVIEW_HEADER_GAP: int = 4
_PREVIEW_LINE_HEIGHT: int = 13
_TOKENS_PER_LINE: int = 3


@dataclass(frozen=True)
class BracketGenState:
    """Everything the generator screen needs to render itself."""

    reference: Shot
    brightest: bool
    ev_step: float
    n: int
    iso1: int
    iso2: Optional[int]      # None → "off"
    field_cursor: int        # 0..7, index into the field list
    config_name: str


def _direction_label(brightest: bool) -> str:
    return format_direction(DIRECTION_BRIGHTEST if brightest else DIRECTION_DARKEST)


def _fields(state: BracketGenState) -> List[Tuple[str, str]]:
    """Flat (label, value) list for the 8 generator fields (cursor order)."""
    iso2_disp = ISO2_OFF if state.iso2 is None else state.iso2
    return [
        ("ref shutter", state.reference.format_shutter()),
        ("ref iso", str(state.reference.iso)),
        ("ref aperture", state.reference.format_aperture()),
        ("direction", _direction_label(state.brightest)),
        ("EV step", format_ev_step(state.ev_step)),
        ("shots", str(state.n)),
        ("iso 1", str(state.iso1)),
        ("iso 2", format_iso2(iso2_disp)),
    ]


def _spec(state: BracketGenState) -> BracketSpec:
    return BracketSpec(
        reference=state.reference,
        brightest=state.brightest,
        ev_step=state.ev_step,
        n=state.n,
        iso1=state.iso1,
        iso2=state.iso2,
    )


def _preview(state: BracketGenState) -> BracketResult:
    return generate_bracket(_spec(state), shutter_grid=SHUTTER_VALUES)


def _token(shot: Shot) -> str:
    """Compact `shutter·iso` preview token."""
    return f"{shot.format_shutter()}·{shot.iso}"


def render_bracket_gen(state: BracketGenState) -> Image.Image:
    """Render the generator screen (320×240, pure).

    Title + 8 field rows + a separator + the live preview block (the
    surviving ladder brightest→darkest plus the dropped count), computed
    by calling `generate_bracket` internally.
    """
    canvas = new_canvas(theme.BG)
    draw = ImageDraw.Draw(canvas)
    font = fonts.mono(_BODY_PT)

    # Title bar.
    draw.text(
        (4, _TITLE_Y), f"GEN BRACKET · {state.config_name}",
        font=font, fill=theme.FG,
    )
    draw.line([(0, _TITLE_LINE_Y), (WIDTH, _TITLE_LINE_Y)], fill=theme.SEP)

    # Field rows.
    fields = _fields(state)
    y = _FIELDS_TOP_Y
    for idx, (label, value) in enumerate(fields):
        active = idx == state.field_cursor
        if active:
            draw.rectangle([0, y - 1, WIDTH - 1, y + 12], fill=theme.SEL_BG)
            draw.rectangle([0, y - 1, 3, y + 12], fill=theme.SEL_BAR)
            lc = vc = theme.SEL_FG
        else:
            lc = vc = theme.FG
        draw.text((8, y), label, font=font, fill=lc)
        vw = widgets.text_width(draw, value, font)
        draw.text((WIDTH - 8 - vw, y), value, font=font, fill=vc)
        y += _ROW_HEIGHT

    # Separator between fields and the preview block.
    sep_y = y + _SEP_GAP
    draw.line([(8, sep_y), (WIDTH - 8, sep_y)], fill=theme.SEP)

    # Preview header: `Preview (S/N):` plus the dropped note when any
    # requested rung fell out of range.
    result = _preview(state)
    surviving = len(result.shots)
    header = f"Preview ({surviving}/{result.requested}):"
    if result.dropped > 0:
        header += f"  {result.dropped} dropped (out of range)"
    hy = sep_y + _PREVIEW_HEADER_GAP
    draw.text((4, hy), header, font=font, fill=theme.FG)

    # Surviving shots as `shutter·iso` tokens, brightest→darkest, wrapped
    # a few per line, pinned above the footer.
    bottom_y = HEIGHT - theme.FOOTER_HEIGHT
    ty = hy + _PREVIEW_LINE_HEIGHT
    tokens = [_token(s) for s in result.shots]
    for i in range(0, len(tokens), _TOKENS_PER_LINE):
        if ty + _PREVIEW_LINE_HEIGHT > bottom_y:
            break
        line = "  ".join(tokens[i:i + _TOKENS_PER_LINE])
        draw.text((6, ty), line, font=font, fill=theme.FG)
        ty += _PREVIEW_LINE_HEIGHT

    widgets.footer(draw, "↑↓ field  ←→ value  OK make  ESC")
    return canvas


# ----------------------------------------------------------------------
# Interaction
# ----------------------------------------------------------------------


class BracketGenAction(str, Enum):
    """External result of a button event on the generator screen."""

    ACCEPT = "accept"   # OK → materialise the ladder into the edit draft
    CANCEL = "cancel"   # BACK → discard, return to edit unchanged


# Default reference when the edit draft is in auto mode (no first shot).
# Matches `EditScreenInteraction._restore_shots`'s default.
_DEFAULT_REFERENCE = Shot(shutter=1 / 30, iso=200, aperture=None)


class BracketGenInteraction:
    """Field cursor + value cycling for the generator screen.

    Holds the live parameter draft; LEFT/RIGHT cycle the focused field
    and the preview recomputes from the current state on every render.
    """

    def __init__(self, *, reference: Shot, config_name: str) -> None:
        self._reference: Shot = reference
        self._config_name: str = config_name
        # Defaults chosen to produce a clean, non-dropping ladder for the
        # default reference (PRD §3): darkest, 1 EV, 5 shots, iso1 = the
        # reference ISO, iso2 off.
        self._brightest: bool = False
        self._ev_step: float = 1
        self._n: int = 5
        self._iso1: int = reference.iso
        # Stored as a raw ISO2_VALUES element ("off" or an int).
        self._iso2 = ISO2_OFF
        self._cursor: int = 0

    @property
    def state(self) -> BracketGenState:
        return BracketGenState(
            reference=self._reference,
            brightest=self._brightest,
            ev_step=self._ev_step,
            n=self._n,
            iso1=self._iso1,
            iso2=(None if self._iso2 == ISO2_OFF else self._iso2),
            field_cursor=self._cursor,
            config_name=self._config_name,
        )

    def result(self) -> BracketResult:
        """The currently generated ladder."""
        return _preview(self.state)

    def on_press(self, button: ButtonId) -> Optional[BracketGenAction]:
        if button == ButtonId.UP:
            self._cursor = max(0, self._cursor - 1)
            return None
        if button == ButtonId.DOWN:
            self._cursor = min(_N_FIELDS - 1, self._cursor + 1)
            return None
        if button in (ButtonId.LEFT, ButtonId.RIGHT):
            self._cycle(-1 if button == ButtonId.LEFT else +1)
            return None
        if button == ButtonId.OK:
            return BracketGenAction.ACCEPT
        if button == ButtonId.BACK:
            return BracketGenAction.CANCEL
        return None

    def _cycle(self, delta: int) -> None:
        c = self._cursor
        if c == 0:
            new = cycle_in_list(self._reference.shutter, SHUTTER_VALUES, delta)
            self._reference = replace(self._reference, shutter=new)
        elif c == 1:
            new = cycle_in_list(self._reference.iso, ISO_VALUES, delta)
            self._reference = replace(self._reference, iso=new)
        elif c == 2:
            new = cycle_in_list(self._reference.aperture, APERTURE_VALUES, delta)
            self._reference = replace(self._reference, aperture=new)
        elif c == 3:
            cur = DIRECTION_BRIGHTEST if self._brightest else DIRECTION_DARKEST
            new = cycle_in_list(cur, DIRECTION_VALUES, delta)
            self._brightest = new == DIRECTION_BRIGHTEST
        elif c == 4:
            self._ev_step = cycle_in_list(self._ev_step, EV_STEP_VALUES, delta)
        elif c == 5:
            self._n = cycle_in_list(self._n, BRACKET_N_VALUES, delta)
        elif c == 6:
            self._iso1 = cycle_in_list(self._iso1, ISO_VALUES, delta)
        elif c == 7:
            self._iso2 = cycle_in_list(self._iso2, ISO2_VALUES, delta)
