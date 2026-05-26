"""UI drawing primitives: status bar, footer, config block, shot row.

These functions take an `ImageDraw` and coordinates; they don't open
or close the canvas. The idea is that screens (main / edit / overlays)
compose these primitives, and the mockups under `docs/mockups/` reuse
exactly the same code so design ↔ implementation stays in sync.
"""

from __future__ import annotations

from typing import Optional

from PIL import ImageDraw

from ..configs import Shot, TimelapseConfig
from ..display.iface import HEIGHT, WIDTH
from . import fonts, theme

# Body font at 11px — all list / button text.
_BODY_PT = 11


def text_width(draw: ImageDraw.ImageDraw, s: str, font) -> int:
    """Pixel width of `s` rendered with the given font."""
    try:
        return int(draw.textlength(s, font=font))
    except Exception:
        return font.getbbox(s)[2]


# Legacy alias: internal functions in this module still call `_text_w`.
# Not public API (importers should use `text_width`).
_text_w = text_width


def status_bar(
    draw: ImageDraw.ImageDraw,
    *,
    time_str: str,
    cam_connected: bool,
    skips: int = 0,
    show_skips: bool = True,
) -> None:
    """Top bar. Occupies up to y=18 (separator line included)."""
    font = fonts.mono(_BODY_PT)
    draw.text((4, 2), time_str, font=font, fill=theme.FG)
    draw.text((78, 2), "fp", font=font, fill=theme.FG)
    cx, cy, cr = 100, 8, 3
    dot = theme.OK_DOT if cam_connected else theme.ERR
    draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=dot)
    if show_skips:
        s = f"SKIPS {skips}"
        sw = _text_w(draw, s, font)
        col = theme.WARN if skips > 0 else theme.FG
        draw.text((WIDTH - 6 - sw, 2), s, font=font, fill=col)
    draw.line(
        [(0, theme.STATUS_BAR_Y_LINE), (WIDTH, theme.STATUS_BAR_Y_LINE)],
        fill=theme.SEP,
    )


def footer(
    draw: ImageDraw.ImageDraw,
    hint: str,
    *,
    version_stamp: Optional[str] = None,
) -> None:
    """Bottom strip with the button hint text.

    The hint is always left-aligned (primary information, where the
    eye starts reading). If `version_stamp` is given, it renders
    bottom-right in DIM colour — metadata, secondary.
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
) -> int:
    """Vertical pixels taken by a config block (header + shots [+ sub]).

    Auto-mode configs render one "1 (auto)" placeholder row instead of
    iterating over shots; height accounts for that.
    """
    rows = 1 if is_auto else n_shots
    h = theme.HEADER_HEIGHT + theme.ROW_HEIGHT * rows + 4
    if running:
        h += theme.ROW_HEIGHT
    return h


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
    h = config_block_height(n, running=running, is_auto=is_auto)
    if selected:
        selection_band(draw, y, h - 4)
    draw_header_row(draw, y, cfg.name, _format_summary(cfg),
                    on_selected=selected, running=running)
    yy = y + theme.HEADER_HEIGHT - 1
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
