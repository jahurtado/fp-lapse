"""Edit screen for a `TimelapseConfig` (§7.3 of docs/reference.md).

Pure rendering: given an `EditState` it produces a 320x240 image
without touching any hardware. Cursor navigation and value cycling
live alongside in `EditScreenInteraction`.

The list of editable fields depends on whether the config is in
**auto mode** (cfg.is_auto, shots is empty tuple) or **manual mode**.

Manual:

  0. name           (read-only from the UI — §7.6, no on-screen keyboard)
  1. interval
  2. shots          (cycle "1 (auto)", 1, 2, …, 9)
  3. #1 shutter
  4. #1 iso
  5. #1 aperture
  6. #2 shutter
  ...

Auto:

  0. name
  1. interval
  2. shots          (currently "1 (auto)")

  A subtle "camera meters every shot" hint is drawn where the manual
  shot rows would otherwise appear.

Between `interval` (idx 1) and `shots` (idx 2), and between `shots`
(idx 2) and `#1 shutter` (idx 3), a thin horizontal spacer line is
rendered. There is no spacer between shots.

When the list doesn't fit, the trailing rows are truncated: the cut
row is the first whose height would exceed the footer strip. The
caller is responsible for keeping `scroll_offset` so the active field
stays visible.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from ..buttons.iface import ButtonId
from ..configs import MAX_SHOTS_PER_BRACKET, Shot, TimelapseConfig
from ..display.iface import HEIGHT, WIDTH, new_canvas
from . import fonts, theme, widgets
from ..schedule.moment import ScheduledMoment
from .edit_values import (
    APERTURE_VALUES,
    INTERVALS_S,
    ISO_VALUES,
    SHOTS_AUTO,
    SHOTS_VALUES,
    SHUTTER_VALUES,
    cycle_in_list,
    format_shots,
)

_BODY_PT: int = 11

# After which indices (in the flat editable list) a spacer is inserted.
# prd2.md §6.2: the schedule pair `start`/`end` (indices 3 and 4) sits
# between `shots` and the first per-shot row, fenced by spacers on both
# sides so the editor reads as three groups (header / schedule / shots).
_SPACER_AFTER = frozenset({1, 2, 4})

_TITLE_Y: int = 2
_TITLE_LINE_Y: int = 18
_FIELDS_TOP_Y: int = 22
_ROW_HEIGHT: int = 13
_SPACER_HEIGHT: int = 10


@dataclass(frozen=True)
class EditState:
    """Everything the edit screen needs to render itself."""

    cfg: TimelapseConfig
    field_cursor: int              # index into the flat editable list
    scroll_offset: int = 0         # first visible field


class EditScreen:
    def render(self, state: EditState) -> Image.Image:
        canvas = new_canvas(theme.BG)
        draw = ImageDraw.Draw(canvas)
        font = fonts.mono(_BODY_PT)

        # Title bar
        draw.text(
            (4, _TITLE_Y),
            f"EDIT · {state.cfg.name}",
            font=font, fill=theme.FG,
        )
        draw.line(
            [(0, _TITLE_LINE_Y), (WIDTH, _TITLE_LINE_Y)],
            fill=theme.SEP,
        )

        fields = editable_fields(state.cfg)
        bottom_y = HEIGHT - theme.FOOTER_HEIGHT  # 224

        y = _FIELDS_TOP_Y
        for idx in range(state.scroll_offset, len(fields)):
            if y + _ROW_HEIGHT > bottom_y:
                break
            label, value = fields[idx]
            active = idx == state.field_cursor
            if active:
                draw.rectangle(
                    [0, y - 1, WIDTH - 1, y + 12], fill=theme.SEL_BG,
                )
                draw.rectangle(
                    [0, y - 1, 3, y + 12], fill=theme.SEL_BAR,
                )
                lc = vc = theme.SEL_FG
            else:
                lc = vc = theme.FG
            draw.text((8, y), label, font=font, fill=lc)
            vw = widgets.text_width(draw, value, font)
            draw.text((WIDTH - 8 - vw, y), value, font=font, fill=vc)
            y += _ROW_HEIGHT
            if idx in _SPACER_AFTER and y + _SPACER_HEIGHT <= bottom_y:
                draw.line(
                    [(8, y + 5), (WIDTH - 8, y + 5)],
                    fill=theme.SEP,
                )
                y += _SPACER_HEIGHT

        # In auto mode, no per-shot rows follow — show a hint so the
        # empty space below "Shots: 1 (auto)" reads intentionally.
        if state.cfg.is_auto and y + _ROW_HEIGHT <= bottom_y:
            draw.text(
                (8, y), "camera meters every shot",
                font=font, fill=theme.DIM,
            )

        widgets.footer(draw, "↑↓ field   ←→ edit   OK save   BACK")
        return canvas


def editable_fields(cfg: TimelapseConfig) -> List[Tuple[str, str]]:
    """Flat (label, value) list of editable fields.

    Always emits name, interval, shots, **start, end**. Then in manual
    mode three per-shot rows follow per Shot. In auto mode the schedule
    pair is still emitted between `shots` and any subsequent rows.
    """
    shots_value = format_shots(SHOTS_AUTO if cfg.is_auto else len(cfg.shots))
    fields: List[Tuple[str, str]] = [
        ("name", cfg.name),
        ("interval", _fmt_interval_value(cfg.interval_s)),
        ("shots", shots_value),
        ("start", _fmt_moment_value(cfg.start)),
        ("end", _fmt_moment_value(cfg.end)),
    ]
    for i, shot in enumerate(cfg.shots, start=1):
        fields.append((f"#{i} shutter", shot.format_shutter()))
        fields.append((f"#{i} iso", _fmt_iso_value(shot.iso)))
        fields.append((f"#{i} aperture", _fmt_aperture_value(shot.aperture)))
    return fields


def _fmt_interval_value(s: float) -> str:
    if s == int(s):
        return f"{int(s)} s"
    return f"{s:g} s"


def _fmt_iso_value(iso: int) -> str:
    return str(iso)


def _fmt_moment_value(moment: Optional[ScheduledMoment]) -> str:
    """Edit-row display of a `ScheduledMoment` (prd2.md §6.2).

    - `None`          → `—`
    - daily (no date) → `HH:MM:SS`
    - one-shot        → `YYYY-MM-DD HH:MM:SS`
    """
    if moment is None:
        return "—"
    if moment.date is None:
        return moment.time.strftime("%H:%M:%S")
    return f"{moment.date.isoformat()} {moment.time.strftime('%H:%M:%S')}"


def _fmt_aperture_value(aperture) -> str:
    if aperture is None:
        return "—"
    v = float(aperture)
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.1f}"


# ----------------------------------------------------------------------
# Edit screen interaction
# ----------------------------------------------------------------------


class EditAction(str, Enum):
    """External result of a button event on EditScreen.

    Value cycling and cursor moves are internal (they mutate
    `interaction.draft` and `interaction.field_cursor`); these returns
    only appear when the user wants to leave the screen.
    """

    SAVE = "save"   # OK pressed
    BACK = "back"   # BACK pressed
    # prd2.md §6.2 — OK on a START/END row opens the digit picker.
    OPEN_PICKER_START = "open_picker_start"
    OPEN_PICKER_END = "open_picker_end"


# Approximate visible window: ~12 editable fields fit between the
# title bar (y=18) and the footer (y=224). It's an approximation —
# actual height depends on how many spacers land. Keeps the cursor
# visible at all times.
_VISIBLE_FIELDS: int = 12


class EditScreenInteraction:
    """Field cursor + value cycling for the edit screen.

    Keeps a `draft` (mutable copy of the `TimelapseConfig` being
    edited) that the cycling actions update. The caller persists
    `interaction.draft` when `EditAction.SAVE` is returned and
    discards on `EditAction.BACK` (ideally opening the discard overlay
    if `is_dirty`).

    `_manual_shots_snapshot` is a UX convenience: when the user
    toggles Shots from manual N → auto and then back to manual, the
    previously configured shots are restored instead of reverting to
    a single default shot. The snapshot persists only within the
    current edit session.
    """

    def __init__(self, cfg: TimelapseConfig) -> None:
        self.original: TimelapseConfig = cfg
        self.draft: TimelapseConfig = cfg
        self.field_cursor: int = 0
        self.scroll_offset: int = 0
        # Save the original manual shots so toggling auto↔manual
        # doesn't lose them. None when the edit started in auto mode
        # — in that case the first toggle to manual builds a fresh
        # 1-shot bracket.
        self._manual_shots_snapshot: Optional[tuple] = (
            cfg.shots if cfg.shots else None
        )

    @property
    def is_dirty(self) -> bool:
        return self.draft != self.original

    def on_press(self, button: ButtonId) -> Optional[EditAction]:
        n_fields = _num_editable_fields(self.draft)
        if button == ButtonId.UP:
            self._move_cursor(-1, n_fields)
            return None
        if button == ButtonId.DOWN:
            self._move_cursor(+1, n_fields)
            return None
        if button in (ButtonId.LEFT, ButtonId.RIGHT):
            # Addendum F: on START/END both LEFT and RIGHT open the
            # picker. Datetime values aren't enumerable, and the picker
            # now hosts both value editing AND mode switching (via the
            # mode chip), so the in-place cycler is gone. Other fields
            # keep the cycler semantics unchanged.
            kind, _ = _field_kind(self.field_cursor, self.draft)
            if kind == "start":
                return EditAction.OPEN_PICKER_START
            if kind == "end":
                return EditAction.OPEN_PICKER_END
            self._cycle_value(-1 if button == ButtonId.LEFT else +1)
            return None
        if button == ButtonId.OK:
            # Addendum F: OK uniformly means SAVE on every field. The
            # picker for START/END is reached via LEFT/RIGHT now.
            return EditAction.SAVE
        if button == ButtonId.BACK:
            return EditAction.BACK
        return None

    def _move_cursor(self, delta: int, n_fields: int) -> None:
        new = max(0, min(n_fields - 1, self.field_cursor + delta))
        self.field_cursor = new
        if new < self.scroll_offset:
            self.scroll_offset = new
        elif new >= self.scroll_offset + _VISIBLE_FIELDS:
            self.scroll_offset = new - _VISIBLE_FIELDS + 1

    def _cycle_value(self, delta: int) -> None:
        kind, shot_idx = _field_kind(self.field_cursor, self.draft)
        if kind == "name":
            return  # read-only (§7.6)
        if kind == "interval":
            new_v = cycle_in_list(self.draft.interval_s, INTERVALS_S, delta)
            self.draft = replace(self.draft, interval_s=float(new_v))
            return
        if kind == "shots":
            self._cycle_shots(delta)
            return
        if shot_idx is None:
            return
        if kind == "shutter":
            self._update_shot_param(shot_idx, "shutter", SHUTTER_VALUES, delta)
        elif kind == "iso":
            self._update_shot_param(shot_idx, "iso", ISO_VALUES, delta)
        elif kind == "aperture":
            self._update_shot_param(shot_idx, "aperture", APERTURE_VALUES, delta)

    def _cycle_shots(self, delta: int) -> None:
        """Move through the Shots cycle: `1 (auto)`, 1, 2, …, 9, wrap.

        The snapshot is updated by **merging** the current draft into
        the front of the previous snapshot, so that the tail (values
        for slots the user just trimmed away) is preserved for a
        round-trip back up. Per-position edits the user made while in
        manual also propagate into the snapshot.
        """
        current = SHOTS_AUTO if self.draft.is_auto else len(self.draft.shots)
        new_val = cycle_in_list(current, SHOTS_VALUES, delta)
        if not self.draft.is_auto:
            self._merge_into_snapshot(self.draft.shots)
        if new_val == SHOTS_AUTO:
            self.draft = replace(self.draft, shots=())
            self._clamp_cursor()
            return
        target_n = int(new_val)
        new_shots = self._restore_shots(target_n)
        self.draft = replace(self.draft, shots=new_shots)
        self._clamp_cursor()

    def _merge_into_snapshot(self, current: tuple) -> None:
        """Overwrite the first `len(current)` snapshot slots with the
        current draft values, keeping anything beyond intact."""
        if not current:
            return
        snap = self._manual_shots_snapshot or ()
        merged = tuple(current) + tuple(snap[len(current):])
        self._manual_shots_snapshot = merged

    def _restore_shots(self, target_n: int) -> tuple:
        """Produce a `target_n`-length shots tuple from the snapshot.

        Falls back to a default 1-shot when there's no snapshot yet
        (edit started in auto mode). If the snapshot is shorter than
        `target_n`, extra slots inherit the last shot's values (§7.3).
        """
        snap = self._manual_shots_snapshot
        if not snap:
            snap = (Shot(shutter=1 / 30, iso=200, aperture=None),)
            self._manual_shots_snapshot = snap
        if len(snap) >= target_n:
            return tuple(snap[:target_n])
        last = snap[-1]
        return tuple(snap) + tuple(last for _ in range(target_n - len(snap)))

    def _clamp_cursor(self) -> None:
        n_fields_now = _num_editable_fields(self.draft)
        if self.field_cursor >= n_fields_now:
            self.field_cursor = n_fields_now - 1

    def _update_shot_param(
        self,
        shot_idx: int,
        param: str,
        values: List,
        delta: int,
    ) -> None:
        shot = self.draft.shots[shot_idx]
        cur_val = getattr(shot, param)
        new_val = cycle_in_list(cur_val, values, delta)
        new_shot = replace(shot, **{param: new_val})
        new_shots = tuple(
            new_shot if i == shot_idx else s
            for i, s in enumerate(self.draft.shots)
        )
        self.draft = replace(self.draft, shots=new_shots)


def _num_editable_fields(cfg: TimelapseConfig) -> int:
    """Number of navigable fields (no spacers).

    5 header (name, interval, shots, start, end) + 3 per shot.
    Auto mode keeps the 5 header rows (start/end are always editable)
    and contributes no per-shot rows.
    """
    return 5 + 3 * len(cfg.shots)


def _field_kind(
    idx: int, cfg: TimelapseConfig,
) -> Tuple[str, Optional[int]]:
    """Map a field index to (kind, shot_idx).

    Layout: 0=name, 1=interval, 2=shots, 3=start, 4=end, then for shot
    k=0..N-1: 5+3k=shutter, 6+3k=iso, 7+3k=aperture. In auto mode only
    0..4 are valid.
    """
    if idx == 0:
        return ("name", None)
    if idx == 1:
        return ("interval", None)
    if idx == 2:
        return ("shots", None)
    if idx == 3:
        return ("start", None)
    if idx == 4:
        return ("end", None)
    if cfg.is_auto:
        # Should never happen — _num_editable_fields caps the cursor —
        # but be defensive.
        return ("end", None)
    shot_idx = (idx - 5) // 3
    param = ("shutter", "iso", "aperture")[(idx - 5) % 3]
    return (param, shot_idx)
