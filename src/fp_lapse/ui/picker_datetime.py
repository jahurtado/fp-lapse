"""Datetime digit picker overlay (prd2.md §6.3).

A child overlay launched from:
  - the Edit screen on a START/END field (mode derived from the field's
    current sub-mode: `TIME` for daily, `DATE_TIME` for one-shot), or
  - the TIME SETUP menu's "Set manually" item (target_field
    `system_clock`, mode forced to `DATE_TIME`).

UX:
  - LEFT / RIGHT moves the cursor between editable digit positions,
    skipping separators.
  - UP / DOWN cycles the current digit 0..9 with wrap.
  - OK validates the digits via `picker_validate.validate_time_digits`.
    Valid → returns `PickerAction.SAVE`. Invalid → returns `None`,
    populates `state.error`, and stays open.
  - BACK returns `PickerAction.CANCEL`.

The picker carries a `target_field` discriminator (`"start"` | `"end"`
| `"system_clock"`) that the App reads after a SAVE to route the
returned `ScheduledMoment` to the right place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_t, datetime, time as time_t
from enum import Enum
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from ..schedule.moment import ScheduledMoment
from . import fonts, theme, widgets
from .picker_validate import validate_time_digits

_BODY_PT: int = 11


class PickerMode(str, Enum):
    """Picker shape.

    NONE      — `[—]`: no moment, the field gets cleared on save.
                Addendum F; only meaningful for `start` / `end` targets.
    TIME      — HH:MM:SS (6 editable digits, daily recurrence).
    DATE_TIME — YYYY-MM-DD HH:MM:SS (14 editable digits, one-shot).
    """

    NONE = "none"
    TIME = "time"
    DATE_TIME = "date_time"


class PickerAction(str, Enum):
    """Action returned by `DateTimePickerInteraction.on_press(...)`."""

    SAVE = "save"
    CANCEL = "cancel"


# Per-mode count of editable digit positions.
_DIGIT_COUNT = {PickerMode.NONE: 0, PickerMode.TIME: 6, PickerMode.DATE_TIME: 14}

# Cursor sentinel for the mode chip (addendum F). When `cursor == -1`,
# the chip is selected; UP/DOWN there cycle the mode rather than a
# digit. Digit cursors are `0..n-1`.
MODE_CHIP_CURSOR: int = -1

# Per-mode max value for each digit (inclusive). Lets UP cycling wrap
# `9 → 0` always — final correctness lives in
# `validate_time_digits(...)` because tens-of-month / tens-of-day depend
# on other cells.
_PER_DIGIT_MAX_BY_MODE = {
    PickerMode.NONE: (),                                  # no editable digits
    PickerMode.TIME: (5, 9, 5, 9, 5, 9),                  # HH:MM:SS
    PickerMode.DATE_TIME: (
        9, 9, 9, 9,        # YYYY: 0..9999 (further bounded by validator)
        1, 9,              # MM:  10..12 etc — bounded by validator
        3, 9,              # DD:  1..31 — bounded by validator
        2, 9,              # HH:  0..23
        5, 9,              # MM:  0..59
        5, 9,              # SS:  0..59
    ),
}


@dataclass(frozen=True)
class PickerState:
    """Immutable snapshot of the picker for rendering."""

    digits: Tuple[int, ...]
    cursor: int                  # `-1` (MODE_CHIP_CURSOR) selects the chip
    mode: PickerMode
    error: Optional[str] = None
    # Addendum F: when False, the renderer hides the mode chip — used by
    # the system_clock flow where the mode is locked to DATE_TIME.
    show_mode_chip: bool = True


def _digits_from_dt(dt: datetime, mode: PickerMode) -> Tuple[int, ...]:
    h, m, s = dt.hour, dt.minute, dt.second
    if mode == PickerMode.TIME:
        return (h // 10, h % 10, m // 10, m % 10, s // 10, s % 10)
    y, mo, d = dt.year, dt.month, dt.day
    return (
        (y // 1000) % 10, (y // 100) % 10, (y // 10) % 10, y % 10,
        mo // 10, mo % 10,
        d // 10, d % 10,
        h // 10, h % 10,
        m // 10, m % 10,
        s // 10, s % 10,
    )


def _digits_from_moment(
    moment: Optional[ScheduledMoment], mode: PickerMode,
) -> Tuple[int, ...]:
    """Convert an optional moment to the digit tuple for `mode`.

    `None` defaults to today at 00:00:00 for DATE_TIME, midnight for
    TIME. The App is responsible for providing a non-None initial value
    in the `system_clock` flow.
    """
    if mode == PickerMode.NONE:
        return ()
    if moment is None:
        if mode == PickerMode.TIME:
            return (0, 0, 0, 0, 0, 0)
        today = date_t.today()
        dt = datetime(today.year, today.month, today.day, 0, 0, 0)
        return _digits_from_dt(dt, mode)
    if mode == PickerMode.TIME:
        h, m, s = moment.time.hour, moment.time.minute, moment.time.second
        return (h // 10, h % 10, m // 10, m % 10, s // 10, s % 10)
    # DATE_TIME — fall back to today if the moment had no date.
    if moment.date is None:
        d = date_t.today()
    else:
        d = moment.date
    dt = datetime(d.year, d.month, d.day,
                  moment.time.hour, moment.time.minute, moment.time.second)
    return _digits_from_dt(dt, mode)


def _parse_digits(
    digits: Tuple[int, ...], mode: PickerMode,
) -> Tuple[Optional[int], Optional[int], Optional[int], int, int, int]:
    """Slice the digit tuple into (year, month, day, hour, minute, second).

    Year/month/day are None in TIME mode.
    """
    if mode == PickerMode.TIME:
        h = digits[0] * 10 + digits[1]
        m = digits[2] * 10 + digits[3]
        s = digits[4] * 10 + digits[5]
        return (None, None, None, h, m, s)
    y = digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]
    mo = digits[4] * 10 + digits[5]
    d = digits[6] * 10 + digits[7]
    h = digits[8] * 10 + digits[9]
    m = digits[10] * 10 + digits[11]
    s = digits[12] * 10 + digits[13]
    return (y, mo, d, h, m, s)


class DateTimePickerInteraction:
    """Cursor navigation + button-to-action translation for the picker."""

    def __init__(
        self,
        *,
        target_field: str,
        initial_value: Optional[ScheduledMoment] = None,
        mode: Optional[PickerMode] = None,
    ) -> None:
        """Construct.

        `target_field` is the discriminator the App reads on commit to
        route the produced moment (`"start"` | `"end"` | `"system_clock"`).
        `"system_clock"` FORCES `mode = DATE_TIME`. For `"start"` /
        `"end"` the mode comes from the field's current editor sub-mode
        (defaults to DATE_TIME if `initial_value` carries a date, else
        TIME).
        """
        self._target_field = target_field
        if target_field == "system_clock":
            self._mode = PickerMode.DATE_TIME
        elif mode is not None:
            self._mode = mode
        elif initial_value is None:
            # Addendum F: the picker reflects the field's current state;
            # for an unset start/end the chip starts at NONE.
            self._mode = PickerMode.NONE
        elif initial_value.date is not None:
            self._mode = PickerMode.DATE_TIME
        else:
            self._mode = PickerMode.TIME
        self._digits: List[int] = list(_digits_from_moment(initial_value, self._mode))
        # Per-mode digit cache so cycling NONE → TIME → DATE_TIME →
        # NONE restores whatever the operator typed previously (rather
        # than resetting to defaults each time).
        self._digit_cache: dict = {self._mode: list(self._digits)}
        # Initial cursor: NONE mode has no digits, so the chip is the
        # only landable position. Any other mode starts on the first
        # editable digit so the operator can type immediately; LEFT
        # one step reaches the chip when they need to switch modes
        # or clear the field. For system_clock the chip is hidden
        # and the cursor cannot be the chip sentinel.
        if self._mode == PickerMode.NONE:
            self._cursor = MODE_CHIP_CURSOR
        else:
            self._cursor: int = 0
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------
    @property
    def target_field(self) -> str:
        return self._target_field

    @property
    def mode(self) -> PickerMode:
        return self._mode

    @property
    def state(self) -> PickerState:
        return PickerState(
            digits=tuple(self._digits),
            cursor=self._cursor,
            mode=self._mode,
            error=self._error,
            show_mode_chip=(self._target_field != "system_clock"),
        )

    @property
    def is_clear_request(self) -> bool:
        """Addendum F: True iff the picker's current mode is NONE.

        The App reads this before calling `commit()` to disambiguate
        "the operator saved a NONE moment (clear the field)" from
        "the operator saved but validation failed (None means error)".
        """
        return self._mode == PickerMode.NONE

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def on_press(self, button: ButtonId) -> Optional[PickerAction]:
        n = _DIGIT_COUNT[self._mode]
        chip_visible = self._target_field != "system_clock"
        if button == ButtonId.LEFT:
            if self._cursor == MODE_CHIP_CURSOR:
                pass  # already leftmost
            elif self._cursor == 0 and chip_visible:
                self._cursor = MODE_CHIP_CURSOR
            else:
                self._cursor = max(0, self._cursor - 1)
            return None
        if button == ButtonId.RIGHT:
            if self._cursor == MODE_CHIP_CURSOR:
                if n > 0:
                    self._cursor = 0
                # else: stays on chip (NONE mode, no digits)
            else:
                self._cursor = min(n - 1, self._cursor + 1)
            return None
        if button == ButtonId.UP:
            if self._cursor == MODE_CHIP_CURSOR:
                self._cycle_mode(+1)
            else:
                self._cycle_current(+1)
            return None
        if button == ButtonId.DOWN:
            if self._cursor == MODE_CHIP_CURSOR:
                self._cycle_mode(-1)
            else:
                self._cycle_current(-1)
            return None
        if button == ButtonId.OK:
            return self._try_save()
        if button == ButtonId.BACK:
            return PickerAction.CANCEL
        return None

    def _cycle_current(self, delta: int) -> None:
        per_max = _PER_DIGIT_MAX_BY_MODE[self._mode][self._cursor]
        n_vals = per_max + 1
        new_v = (self._digits[self._cursor] + delta) % n_vals
        self._digits[self._cursor] = new_v
        # Editing the value clears any stale error from the previous OK.
        self._error = None

    def _cycle_mode(self, delta: int) -> None:
        """Addendum F: cycle the chip through `NONE / TIME / DATE_TIME`.

        System_clock target should never reach this method (its cursor
        cannot land on the chip), but defensively no-op if it did.
        Digit state is cached per-mode so cycling away and back
        restores whatever the operator typed.
        """
        if self._target_field == "system_clock":
            return
        # Snapshot current digits before switching modes.
        self._digit_cache[self._mode] = list(self._digits)
        modes = [PickerMode.NONE, PickerMode.TIME, PickerMode.DATE_TIME]
        idx = modes.index(self._mode)
        self._mode = modes[(idx + delta) % len(modes)]
        if self._mode in self._digit_cache:
            self._digits = list(self._digit_cache[self._mode])
        else:
            self._digits = list(_digits_from_moment(None, self._mode))
        self._error = None
        # Cursor stays on the chip — the operator can RIGHT into the
        # newly-revealed digits when they're ready to type.

    def _try_save(self) -> Optional[PickerAction]:
        if self._mode == PickerMode.NONE:
            # Clearing a field — no digits to validate.
            return PickerAction.SAVE
        result = self.commit_raw()
        if result is None:
            return None
        return PickerAction.SAVE

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------
    def commit(self) -> Optional[ScheduledMoment]:
        """Convert current digits → ScheduledMoment.

        `None` on validation failure (the picker stays open, `error`
        populated). On success, returns the moment AND keeps the error
        cleared.

        Addendum F: returns `None` for `PickerMode.NONE` as well. The
        caller must check `is_clear_request` BEFORE `commit()` to
        disambiguate "clear field" from "validation error".
        """
        if self._mode == PickerMode.NONE:
            return None
        r = self.commit_raw()
        if r is None:
            return None
        date_v, time_v = r
        return ScheduledMoment(time=time_v, date=date_v)

    def commit_raw(self) -> Optional[Tuple[Optional[date_t], time_t]]:
        """Internal: returns (date|None, time) on success, None on failure.

        Both `commit()` and `_try_save()` go through this; the picker's
        `error` field is mutated as a side effect.
        """
        y, mo, d, h, m, s = _parse_digits(tuple(self._digits), self._mode)
        result = validate_time_digits(
            year=y, month=mo, day=d,
            hour=h, minute=m, second=s,
            mode=self._mode.value,
        )
        if not result.ok:
            self._error = result.error
            return None
        self._error = None
        assert result.time is not None
        return (result.date, result.time)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


_TITLE_Y: int = 4
_TITLE_LINE_Y: int = 20
# Addendum H: chip and digits are both **horizontally centred** so the
# layout reads as a single vertical column — chip on top, value below.
# X is computed at render time from the actual rendered widths so the
# chip stays centred even as its label changes (`[—]` / `[TIME]` /
# `[DATE+TIME]` have different widths).
_CHIP_Y: int = 30
_DIGITS_Y: int = 60
_DIGIT_CHAR_W_FALLBACK: int = 8   # Menlo 11px ~ 7-8 px / mono char
_ERROR_Y: int = 110

_CHIP_LABEL = {
    PickerMode.NONE: "[—]",
    PickerMode.TIME: "[TIME]",
    PickerMode.DATE_TIME: "[DATE+TIME]",
}


def _validate_cursor(state: PickerState) -> None:
    """Raise `ValueError` if `state.cursor` is outside the legal range.

    The legal range depends on the mode and whether the chip is visible:
    - NONE mode: only the chip is landable (cursor must be
      `MODE_CHIP_CURSOR`); when the chip is hidden, this is unreachable
      and raises.
    - TIME / DATE_TIME: cursor is `0..len(digits)-1`, plus
      `MODE_CHIP_CURSOR` when the chip is visible.
    """
    if state.mode == PickerMode.NONE:
        if state.cursor != MODE_CHIP_CURSOR or not state.show_mode_chip:
            raise ValueError(
                f"NONE-mode cursor must be MODE_CHIP_CURSOR "
                f"(={MODE_CHIP_CURSOR}) with chip visible, got {state.cursor}"
            )
        return
    min_cursor = MODE_CHIP_CURSOR if state.show_mode_chip else 0
    if not (min_cursor <= state.cursor < len(state.digits)):
        raise ValueError(
            f"cursor must be in [{min_cursor}, {len(state.digits)}), "
            f"got {state.cursor}"
        )


def _layout_template(mode: PickerMode) -> Tuple[str, Tuple[int, ...]]:
    """Return the display string + the index in that string of each
    editable cell (so the renderer can underline / box the cursor).

    Editable cells are listed in left-to-right order (matches
    `state.cursor`).
    """
    if mode == PickerMode.TIME:
        s = "HH:MM:SS"
        cells = (0, 1, 3, 4, 6, 7)
    else:
        s = "YYYY-MM-DD HH:MM:SS"
        cells = (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18)
    return s, cells


def render_datetime_picker(
    base: Image.Image,
    state: PickerState,
    *,
    title: str,
) -> Image.Image:
    """Compose the picker overlay on top of `base`. Returns RGB 320x240."""
    # Addendum G: opaque screen transition — the previous screen
    # content is hidden entirely instead of dimmed-through. The base
    # image is accepted only for size validation; its pixels are
    # discarded so there is no residual transparency.
    rgba, draw = widgets.new_overlay_canvas(base)

    # Addendum F: cursor `-1` (MODE_CHIP_CURSOR) selects the mode chip;
    # `0..len(state.digits)-1` for the digits. NONE mode is the special
    # case where only the chip is landable.
    _validate_cursor(state)

    font = fonts.mono(_BODY_PT)

    # Title bar
    draw.text((4, _TITLE_Y), title, font=font, fill=theme.FG)
    draw.line(
        [(0, _TITLE_LINE_Y), (WIDTH, _TITLE_LINE_Y)],
        fill=theme.SEP,
    )

    # Addendum F + H: mode chip — visible for start/end pickers, hidden
    # for system_clock (mode is locked there). Centred horizontally so
    # the whole picker reads as a single vertical column (chip on top,
    # value below).
    if state.show_mode_chip:
        chip_label = _CHIP_LABEL[state.mode]
        chip_w = int(draw.textlength(chip_label, font=font))
        chip_x = (WIDTH - chip_w) // 2
        is_chip_selected = state.cursor == MODE_CHIP_CURSOR
        if is_chip_selected:
            draw.rectangle(
                [chip_x - 2, _CHIP_Y - 1,
                 chip_x + chip_w + 2, _CHIP_Y + 13],
                fill=theme.SEL_BG,
            )
            chip_fg = theme.SEL_FG
        else:
            chip_fg = theme.DIM
        draw.text((chip_x, _CHIP_Y), chip_label, font=font, fill=chip_fg)

    # NONE mode has no digits to render — skip the digits + cursor band.
    if state.mode != PickerMode.NONE:
        # Build the digit-rendered string from the template (replacing
        # each editable cell with its current digit).
        template, cells = _layout_template(state.mode)
        chars = list(template)
        for cell_idx, str_pos in enumerate(cells):
            chars[str_pos] = str(state.digits[cell_idx])
        rendered = "".join(chars)

        # Addendum H: centre the digit string horizontally — matches the
        # chip above so the picker reads as a centred column.
        digits_w = int(draw.textlength(rendered, font=font))
        digits_x = (WIDTH - digits_w) // 2
        draw.text((digits_x, _DIGITS_Y), rendered, font=font, fill=theme.FG)

        # Cursor underline: only when the cursor is on a digit (not on
        # the chip).
        if state.cursor != MODE_CHIP_CURSOR:
            cursor_pos = cells[state.cursor]
            try:
                prefix_w = int(draw.textlength(rendered[:cursor_pos], font=font))
                char_w = int(draw.textlength(rendered[cursor_pos], font=font))
            except Exception:
                prefix_w = cursor_pos * _DIGIT_CHAR_W_FALLBACK
                char_w = _DIGIT_CHAR_W_FALLBACK
            ux0 = digits_x + prefix_w
            uy = _DIGITS_Y + 13
            draw.line(
                [(ux0, uy), (ux0 + char_w - 1, uy)],
                fill=theme.SEL_BAR,
            )

    # Error line (single dim-red line below the digits).
    if state.error:
        draw.text(
            (4, _ERROR_Y), state.error,
            font=font, fill=theme.ERR,
        )

    # Footer hint
    widgets.footer(
        draw, "↑↓ digit  ←→ pos  OK save  BACK cancel",
    )

    return rgba.convert("RGB")
