"""UI drawing primitives: status bar, footer, config block, shot row.

These functions take an `ImageDraw` and coordinates; they don't open
or close the canvas. The idea is that screens (main / edit / overlays)
compose these primitives, and the mockups under `docs/mockups/` reuse
exactly the same code so design ↔ implementation stays in sync.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PIL import Image, ImageDraw

from ..configs import Shot, TimelapseConfig
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme
from .schedule_indicator import ScheduleIndicator


def new_overlay_canvas(
    base: Image.Image,
) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    """Open a fresh opaque-BG RGBA canvas + an `ImageDraw` for an overlay.

    Addendum G — every overlay renderer (picker, time-setup menu,
    manage menu, confirmation dialogs) shares the same three opening
    lines: validate the base's size, allocate an RGBA canvas filled
    with `theme.BG` (full alpha), and build an `ImageDraw` over it.
    This helper centralises that pattern; callers are still responsible
    for the final `convert("RGB")` on the result they return.

    `base` is accepted only for its size — its pixels are discarded
    on purpose (the overlay is fully opaque, not a translucent shade).
    """
    if base.size != (WIDTH, HEIGHT):
        raise ValueError(f"base must be {WIDTH}x{HEIGHT}, got {base.size}")
    rgba = Image.new("RGBA", (WIDTH, HEIGHT), theme.BG + (255,))
    draw = ImageDraw.Draw(rgba)
    return rgba, draw

# Body font at 11px — all list / button text.
_BODY_PT = 11

# Schedule indicator (prd2.md §6 + addendum A2) — clock pictogram +
# colored dot. The clock is drawn with PIL primitives (a circle outline
# + 12 o'clock + 3 o'clock hands) so it doesn't depend on font availability
# and reads as a clock at 9 px regardless of the Mac/Pi font fallback.
# Only the dot's colour encodes state; the pictogram is always DIM.
_INDICATOR_CLOCK_DIAMETER: int = 9         # circle diameter in px
_INDICATOR_GAP: int = 8     # px between indicator and SKIPS (or right margin)
_INDICATOR_DOT_RADIUS: int = 3
_INDICATOR_DOT_GAP: int = 4   # px between glyph and dot


def _draw_clock_glyph(
    draw: ImageDraw.ImageDraw, x: int, cy: int, color,
) -> int:
    """Draw a tiny clock pictogram at `(x, cy)` (cy is the vertical centre).

    Geometry: a circle outline of `_INDICATOR_CLOCK_DIAMETER` px, plus a
    short vertical line (12 o'clock — the "hour hand") and a short
    horizontal line (3 o'clock — the "minute hand") from the centre to
    the rim. That `12:15` hand position is the universally-recognised
    "this is a clock" cue at small sizes (addendum A2).

    Returns the pixel width consumed (== diameter), so the caller can
    advance the cursor for the colored dot that follows.
    """
    d = _INDICATOR_CLOCK_DIAMETER
    r = d // 2
    cx_center = x + r
    # Circle outline.
    draw.ellipse(
        [x, cy - r, x + d - 1, cy + r],
        outline=color,
    )
    # 12 o'clock hand (vertical, centre → top rim, stopping 1 px short).
    draw.line(
        [(cx_center, cy), (cx_center, cy - (r - 1))],
        fill=color,
    )
    # 3 o'clock hand (horizontal, centre → right rim, stopping 1 px short).
    draw.line(
        [(cx_center, cy), (cx_center + (r - 1), cy)],
        fill=color,
    )
    return d

# Map ScheduleIndicator → dot color. OFF has no entry (handled by the
# caller skipping the whole indicator).
_INDICATOR_DOT_COLOR = {
    ScheduleIndicator.RED: theme.ERR,
    ScheduleIndicator.GREEN: theme.OK_DOT,
    ScheduleIndicator.YELLOW: theme.WARN,
}


def text_width(draw: ImageDraw.ImageDraw, s: str, font) -> int:
    """Pixel width of `s` rendered with the given font."""
    try:
        return int(draw.textlength(s, font=font))
    except Exception:
        return font.getbbox(s)[2]


# Legacy alias: internal functions in this module still call `_text_w`.
# Not public API (importers should use `text_width`).
_text_w = text_width


# Where the camera model label starts (px). The connection dot and any
# warning indicator are placed dynamically to the right of the label so a
# longer model name ("D5600") doesn't collide with the dot. The gap is
# tuned so the legacy "fp" label keeps the dot at its historical x=100
# (preserving the byte-exact mockups): 78 + width("fp")=14 + 8 = 100.
_MODEL_LABEL_X: int = 78
_DOT_GAP: int = 8   # px from the label's right edge to the dot centre
_WARN_GAP: int = 8  # px from the dot to the dial-warning text


def status_bar(
    draw: ImageDraw.ImageDraw,
    *,
    time_str: str,
    cam_connected: bool,
    skips: int = 0,
    show_skips: bool = True,
    model_label: str = "fp",
    dial_mismatch: bool = False,
    schedule_state: ScheduleIndicator = ScheduleIndicator.OFF,
    schedule_disabled: bool = False,
    version_stamp: Optional[str] = None,
) -> None:
    """Top bar. Occupies up to y=18 (separator line included).

    `model_label` is the live camera's short name ("fp" for the Sigma,
    "D5600" for the Nikon); it updates when the camera is hot-swapped.
    `dial_mismatch` shows a `DIAL NOT ON M` warning (WARN colour) when the
    engine's requested exposure mode disagrees with the D5600's physical
    mode dial.

    `schedule_state` (prd2.md §6) controls the schedule indicator
    rendered immediately left of SKIPS: nothing when OFF; a clock
    glyph + colored dot otherwise. Default `OFF` preserves the
    pre-schedule visual contract for every existing call site.

    `version_stamp`, when given, renders at the far-right of the bar
    in DIM colour — metadata, secondary, but persistent so it doesn't
    crowd the footer hints. SKIPS and the schedule indicator shift
    left of it when both are shown. Default `None` preserves the
    pre-version visual contract.
    """
    font = fonts.mono(_BODY_PT)
    draw.text((4, 2), time_str, font=font, fill=theme.FG)
    draw.text((_MODEL_LABEL_X, 2), model_label, font=font, fill=theme.FG)
    label_w = _text_w(draw, model_label, font)
    cx = _MODEL_LABEL_X + label_w + _DOT_GAP
    cy, cr = 8, 3
    dot = theme.OK_DOT if cam_connected else theme.ERR
    draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=dot)
    if dial_mismatch:
        draw.text(
            (cx + cr + _WARN_GAP, 2), "DIAL NOT ON M",
            font=font, fill=theme.WARN,
        )
    # Right-anchored group: version stamp (if any) | SKIPS (if shown)
    # | indicator (if any). Rightmost element renders first so the
    # next one to its left can compute its position.
    right_edge = WIDTH - 6
    if version_stamp is not None:
        sw = _text_w(draw, version_stamp, font)
        draw.text(
            (right_edge - sw, 2), version_stamp,
            font=font, fill=theme.DIM,
        )
        right_edge = right_edge - sw - _INDICATOR_GAP
    if show_skips:
        s = f"SKIPS {skips}"
        sw = _text_w(draw, s, font)
        col = theme.WARN if skips > 0 else theme.FG
        skips_x = right_edge - sw
        draw.text((skips_x, 2), s, font=font, fill=col)
        right_edge = skips_x - _INDICATOR_GAP
    if schedule_state != ScheduleIndicator.OFF:
        dot_color = _INDICATOR_DOT_COLOR[schedule_state]
        # Total width: clock pictogram + gap + dot diameter.
        total_w = (
            _INDICATOR_CLOCK_DIAMETER
            + _INDICATOR_DOT_GAP
            + 2 * _INDICATOR_DOT_RADIUS
        )
        ix = right_edge - total_w
        glyph_w = _draw_clock_glyph(draw, ix, cy, theme.DIM)
        dot_cx = ix + glyph_w + _INDICATOR_DOT_GAP + _INDICATOR_DOT_RADIUS
        draw.ellipse(
            [
                dot_cx - _INDICATOR_DOT_RADIUS, cy - _INDICATOR_DOT_RADIUS,
                dot_cx + _INDICATOR_DOT_RADIUS, cy + _INDICATOR_DOT_RADIUS,
            ],
            fill=dot_color,
        )
        if schedule_disabled:
            # Diagonal strikethrough across the clock glyph — bottom-
            # left to top-right corner of the 9-px circle bbox. Drawn
            # in FG (brighter than the DIM glyph) so it stands out
            # without competing with the colored dot for attention.
            r = _INDICATOR_CLOCK_DIAMETER // 2
            draw.line(
                [(ix, cy + r), (ix + _INDICATOR_CLOCK_DIAMETER - 1, cy - r)],
                fill=theme.FG,
            )
    draw.line(
        [(0, theme.STATUS_BAR_Y_LINE), (WIDTH, theme.STATUS_BAR_Y_LINE)],
        fill=theme.SEP,
    )


def footer(
    draw: ImageDraw.ImageDraw,
    hint: str,
    *,
    hint2: Optional[str] = None,
    version_stamp: Optional[str] = None,
) -> None:
    """Bottom strip with the button hint text.

    Two rows of mono-11. `hint` is the primary line (state-dependent
    actions like `OK run` / `BACK stop`); `hint2` is the optional
    secondary line — the main screen uses it for the global
    LEFT/RIGHT/chord shortcuts. Callers that don't need a second line
    (edit_screen, picker_datetime) omit `hint2` and the bottom row
    stays empty.

    Both lines are left-aligned (primary information, where the eye
    starts reading). If `version_stamp` is given, it renders on the
    primary line, right-aligned in DIM colour — metadata, secondary.
    """
    font = fonts.mono(_BODY_PT)
    y = HEIGHT - theme.FOOTER_HEIGHT
    draw.line([(0, y), (WIDTH, y)], fill=theme.SEP)
    draw.text((4, y + 3), hint, font=font, fill=theme.DIM)
    if version_stamp:
        stamp_w = font.getlength(version_stamp)
        draw.text(
            (WIDTH - 4 - stamp_w, y + 3),
            version_stamp, font=font, fill=theme.DIM,
        )
    if hint2:
        # Second row sits one ROW_HEIGHT below the first, with the
        # same 3 px top padding.
        draw.text((4, y + 3 + theme.ROW_HEIGHT), hint2, font=font, fill=theme.DIM)


BANNER_HEIGHT: int = 14
_BANNER_ERR_BG = (180, 40, 40)
_BANNER_WARN_BG = (180, 130, 0)
_BANNER_FG = (255, 255, 255)


def draw_banner(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    *,
    severity: str = "error",
) -> int:
    """Full-width banner strip. Returns the y just below it.

    `severity` is "error" (red) or "warn" (amber). Used for the
    persistent §6.1 / §6.3 banners — CAMERA NOT RESPONDING and
    CONFIGS RESET.
    """
    bg = _BANNER_ERR_BG if severity == "error" else _BANNER_WARN_BG
    draw.rectangle([0, y, WIDTH - 1, y + BANNER_HEIGHT - 1], fill=bg)
    font = fonts.mono(_BODY_PT)
    draw.text((6, y + 1), text, font=font, fill=_BANNER_FG)
    return y + BANNER_HEIGHT


def selection_band(draw: ImageDraw.ImageDraw, y0: int, height: int) -> None:
    """Light-gray (inverse video) band + left yellow bar."""
    draw.rectangle([0, y0, WIDTH - 1, y0 + height - 1], fill=theme.SEL_BG)
    draw.rectangle([0, y0, 3, y0 + height - 1], fill=theme.SEL_BAR)


def draw_shot_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    *,
    idx: Optional[int],
    shutter: str,
    iso: str,
    aper: str,
    on_selected: bool = False,
) -> None:
    """A single row aligning shutter / ISO / aperture by column."""
    font = fonts.mono(_BODY_PT)
    text_col = theme.SEL_FG if on_selected else theme.FG
    dim_col = theme.SEL_DIM if on_selected else theme.DIM
    if idx is not None:
        draw.text((theme.COL_IDX, y), str(idx), font=font, fill=dim_col)
    draw.text(
        (theme.COL_SHUT, y), shutter, font=font,
        fill=dim_col if shutter == "—" else text_col,
    )
    draw.text(
        (theme.COL_ISO, y), iso, font=font,
        fill=dim_col if iso == "ISO —" else text_col,
    )
    draw.text(
        (theme.COL_APER, y), aper, font=font,
        fill=dim_col if aper == "f/—" else text_col,
    )


def draw_header_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    name: str,
    summary: str,
    *,
    on_selected: bool = False,
    running: bool = False,
) -> None:
    """Config name row + summary (interval + shot count)."""
    font = fonts.mono(_BODY_PT)
    text_col = theme.SEL_FG if on_selected else theme.FG
    x = 8
    if running:
        rx, ry, rr = x + 2, y + 6, 3
        draw.ellipse([rx - rr, ry - rr, rx + rr, ry + rr], fill=theme.RUN_DOT)
        x = rx + rr + 4
    draw.text((x, y), name, font=font, fill=text_col)
    sw = _text_w(draw, summary, font)
    draw.text((WIDTH - 6 - sw, y), summary, font=font, fill=text_col)


def config_block_height(
    n_shots: int, *, running: bool = False, is_auto: bool = False,
    schedule_lines: int = 0,
) -> int:
    """Vertical pixels taken by a config block (header + shots [+ sub]).

    Auto-mode configs render one "1 (auto)" placeholder row instead of
    iterating over shots; height accounts for that.

    `schedule_lines` (addendum E) is the number of extra rows the
    config block needs to display its `start` / `end` moments — 0 if
    neither is set, 1 if they collapse to the same date, 1 if only
    one is set, 2 if both are set with different "shapes" (one daily,
    one one-shot, or different dates). `format_schedule_lines(cfg)`
    is the canonical computer.
    """
    rows = 1 if is_auto else n_shots
    h = theme.HEADER_HEIGHT + theme.ROW_HEIGHT * rows + 4
    if running:
        h += theme.ROW_HEIGHT
    h += theme.ROW_HEIGHT * schedule_lines
    return h


def _format_moment_full(m) -> str:
    """`YYYY-MM-DD HH:MM:SS` for one-shot, `HH:MM:SS` for daily."""
    if m.date is not None:
        return f"{m.date.isoformat()} {m.time.isoformat()}"
    return m.time.isoformat()


def format_schedule_lines(cfg: TimelapseConfig) -> list[str]:
    """Render the optional `start` / `end` moments as 0, 1 or 2 lines.

    Addendum E: main-screen visibility of when a config will fire.
    Returns:
      - `[]` if neither moment is set.
      - `["▶ <time>  ■ <time>"]` (one line) if both are set, both
        one-shot AND on the same date — the date is shown once before
        the two times to save horizontal pixels.
      - `["▶ <full>"]` or `["■ <full>"]` (one line) if only one is set.
      - `["▶ <full>", "■ <full>"]` (two lines) otherwise — different
        shapes (daily vs one-shot) or different dates.
    """
    s, e = cfg.start, cfg.end
    if s is None and e is None:
        return []
    if s is not None and e is not None:
        # Collapse same-date one-shot pair: `YYYY-MM-DD  ▶ HH:MM:SS  ■ HH:MM:SS`
        if (
            s.date is not None and e.date is not None and s.date == e.date
        ):
            return [
                f"{s.date.isoformat()}  "
                f"▶ {s.time.isoformat()}  "
                f"■ {e.time.isoformat()}"
            ]
        return [
            f"▶ {_format_moment_full(s)}",
            f"■ {_format_moment_full(e)}",
        ]
    if s is not None:
        return [f"▶ {_format_moment_full(s)}"]
    assert e is not None  # type-narrowing
    return [f"■ {_format_moment_full(e)}"]


def _format_summary(cfg: TimelapseConfig) -> str:
    if cfg.is_auto:
        return f"{_format_interval(cfg.interval_s)} · 1 shot (auto)"
    n = len(cfg.shots)
    return f"{_format_interval(cfg.interval_s)} · {n} shot{'s' if n != 1 else ''}"


def _format_interval(s: float) -> str:
    if s == int(s):
        return f"{int(s)} s"
    return f"{s:g} s"


def draw_config_block(
    draw: ImageDraw.ImageDraw,
    y: int,
    cfg: TimelapseConfig,
    *,
    selected: bool = False,
    running: bool = False,
    taken: Optional[int] = None,
    next_in_s: Optional[float] = None,
) -> int:
    """Render an entire config block (header + sub + shots).

    Returns the `y` of the first free pixel below the block.
    """
    n = len(cfg.shots)
    is_auto = cfg.is_auto
    sched_lines = format_schedule_lines(cfg)
    h = config_block_height(
        n, running=running, is_auto=is_auto,
        schedule_lines=len(sched_lines),
    )
    if selected:
        selection_band(draw, y, h - 4)
    draw_header_row(draw, y, cfg.name, _format_summary(cfg),
                    on_selected=selected, running=running)
    yy = y + theme.HEADER_HEIGHT - 1
    # Addendum E: schedule lines render directly under the header,
    # ahead of the running-sub and the shots — operator-visible
    # metadata about WHEN the config fires.
    if sched_lines:
        sub_col = theme.SEL_DIM if selected else theme.DIM
        font = fonts.mono(_BODY_PT)
        for line in sched_lines:
            draw.text((22, yy), line, font=font, fill=sub_col)
            yy += theme.ROW_HEIGHT
    if running:
        sub = (
            f"taken {taken if taken is not None else 0}"
            f"   next in {_format_next_in(next_in_s)}"
        )
        sub_col = theme.SEL_DIM if selected else theme.DIM
        font = fonts.mono(_BODY_PT)
        draw.text((22, yy), sub, font=font, fill=sub_col)
        yy += theme.ROW_HEIGHT
    if is_auto:
        # One dim placeholder row — no shot params to print, the camera
        # decides per fire.
        sub_col = theme.SEL_DIM if selected else theme.DIM
        font = fonts.mono(_BODY_PT)
        draw.text((22, yy), "camera meters", font=font, fill=sub_col)
        yy += theme.ROW_HEIGHT
    else:
        for i, shot in enumerate(cfg.shots, start=1):
            idx = i if n > 1 else None
            draw_shot_row(
                draw, yy,
                idx=idx,
                shutter=shot.format_shutter(),
                iso=shot.format_iso(),
                aper=shot.format_aperture(),
                on_selected=selected,
            )
            yy += theme.ROW_HEIGHT
    return y + h


def _format_next_in(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    return f"{seconds:.1f}s"


def draw_new_config_pseudo_item(
    draw: ImageDraw.ImageDraw,
    text_y: int,
    *,
    selected: bool = False,
) -> None:
    """Pseudo-item `+ New configuration` at the bottom of the list (§7.1).

    `text_y` is the exact y where the text goes (not the block top).
    When selected, the wrapping highlight band is drawn around it.
    """
    font = fonts.mono(_BODY_PT)
    if selected:
        selection_band(draw, text_y - 1, theme.ROW_HEIGHT + 2)
        col = theme.SEL_FG
    else:
        col = theme.DIM
    draw.text((8, text_y), "+ New configuration", font=font, fill=col)
