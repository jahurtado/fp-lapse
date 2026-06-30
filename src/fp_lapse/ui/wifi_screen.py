"""Wi-Fi network list + connection status screens (wifi-manual-config §4).

Two render functions and the list interaction. The virtual keyboard
lives in its own module (`keyboard.py`); this module is the **list**
screen (scan results + `Other network…` + `Rescan`) and the **status**
screen (connecting / connected / failed).

Follows the existing UI idioms: frozen `*State` dataclasses + pure
`render_*` functions + a `WifiListInteraction` returning a
`WifiListAction`. The App holds the interaction, dispatches the action,
and runs the blocking `nmcli` work off-thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from PIL import Image

from ..buttons.iface import ButtonId
from ..display.iface import HEIGHT, WIDTH
from ..net.nmcli import ConnectOutcome, WifiNetwork, signal_glyph
from . import fonts, theme, widgets

_BODY_PT: int = 11


# ----------------------------------------------------------------------
# Network list
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class WifiListState:
    networks: Tuple[WifiNetwork, ...]
    cursor: int          # 0..len(items)-1 over [networks..., Other…, Rescan]
    scanning: bool       # True → "Scanning…" animated header


class WifiListAction(str, Enum):
    CONNECT = "connect"      # short OK on a network
    OTHER = "other"          # short OK on "Other network…"
    RESCAN = "rescan"        # short OK on "Rescan"
    EDIT = "edit"            # long-press OK on a secured network
    FORGET = "forget"        # long-press BACK on a saved network
    CANCEL = "cancel"        # short BACK → SETTINGS


class WifiListInteraction:
    """Cursor navigation over [networks…, Other network…, Rescan].

    Revision 1 — connect vs. edit on the network list. OK and BACK are
    deferred to **release** so a short tap and a long hold are
    distinguishable, mirroring `MainScreenInteraction`. UP/DOWN move the
    cursor on press. The four gestures:

      - short OK   → CONNECT / OTHER / RESCAN (by cursor)
      - hold OK    → EDIT, only on a secured network
      - short BACK → CANCEL (back to SETTINGS)
      - hold BACK  → FORGET, only on a saved network

    The `_ok_pressed` / `_back_pressed` flags guard each long-press hook
    against a stale fire (e.g. a held BACK that cancelled the keyboard
    must not then forget on the freshly-shown list) — the same race the
    main screen guards. `reset_input()` clears them when the list
    (re)gains focus.
    """

    def __init__(self, networks: Tuple[WifiNetwork, ...]) -> None:
        self._networks = tuple(networks)
        self.cursor: int = 0
        self._ok_pressed: bool = False
        self._ok_long_fired: bool = False
        self._back_pressed: bool = False
        self._back_long_fired: bool = False

    @property
    def _total(self) -> int:
        return len(self._networks) + 2   # + Other… + Rescan

    @property
    def _other_index(self) -> int:
        return len(self._networks)

    @property
    def _rescan_index(self) -> int:
        return len(self._networks) + 1

    def reset_input(self) -> None:
        """Clear pressed/long-fired flags. Call when the list loses or
        regains focus so a stale OK/BACK doesn't trigger an action."""
        self._ok_pressed = False
        self._ok_long_fired = False
        self._back_pressed = False
        self._back_long_fired = False

    def on_press(self, button: ButtonId) -> Optional[WifiListAction]:
        if button == ButtonId.UP:
            self.cursor = max(0, self.cursor - 1)
            return None
        if button == ButtonId.DOWN:
            self.cursor = min(self._total - 1, self.cursor + 1)
            return None
        if button == ButtonId.OK:
            self._ok_pressed = True
            self._ok_long_fired = False
            return None
        if button == ButtonId.BACK:
            self._back_pressed = True
            self._back_long_fired = False
            return None
        return None  # LEFT/RIGHT don't apply

    def on_release(self, button: ButtonId) -> Optional[WifiListAction]:
        if button == ButtonId.OK:
            if not self._ok_pressed:
                return None
            self._ok_pressed = False
            if self._ok_long_fired:
                return None
            if self.cursor < len(self._networks):
                return WifiListAction.CONNECT
            if self.cursor == self._other_index:
                return WifiListAction.OTHER
            return WifiListAction.RESCAN
        if button == ButtonId.BACK:
            if not self._back_pressed:
                return None
            self._back_pressed = False
            if self._back_long_fired:
                return None
            return WifiListAction.CANCEL
        return None

    def on_long_press(self, button: ButtonId) -> Optional[WifiListAction]:
        """Long-press hooks. OK → EDIT (secured only); BACK → FORGET
        (saved only). Each is guarded by its pressed flag so a stale
        timer fire (after release / across a screen change) is a no-op."""
        if button == ButtonId.OK:
            if not self._ok_pressed:
                return None
            self._ok_long_fired = True
            if (self.cursor < len(self._networks)
                    and self._networks[self.cursor].secured):
                return WifiListAction.EDIT
            return None
        if button == ButtonId.BACK:
            if not self._back_pressed:
                return None
            self._back_long_fired = True
            if (self.cursor < len(self._networks)
                    and self._networks[self.cursor].saved):
                return WifiListAction.FORGET
            return None
        return None

    def selected_network(self, state: WifiListState) -> Optional[WifiNetwork]:
        if 0 <= self.cursor < len(state.networks):
            return state.networks[self.cursor]
        return None


# List geometry.
_LIST_TITLE_Y: int = 4
_LIST_LINE_Y: int = 20
_LIST_TOP: int = 26
_ROW_H: int = 16
_GLYPH_X: int = 8
_SSID_X: int = 34
_LOCK_X: int = WIDTH - 46
_DOT_X: int = WIDTH - 22
_SSID_MAX_CHARS: int = 22


def _draw_lock(draw, x: int, cy: int, color) -> None:
    """Tiny padlock pictogram (drawn, font-independent)."""
    # Body.
    draw.rectangle([x, cy, x + 7, cy + 6], outline=color)
    # Shackle (arc above the body).
    draw.arc([x + 1, cy - 5, x + 6, cy + 2], start=180, end=360, fill=color)


def render_wifi_list(
    base: Image.Image, state: WifiListState, *, dots: Optional[int],
) -> Image.Image:
    """Compose the Wi-Fi network list over `base`. Returns RGB 320x240."""
    rgba, draw = widgets.new_overlay_canvas(base)
    font = fonts.mono(_BODY_PT)

    title = "Wi-Fi setup"
    if state.scanning:
        title = "Scanning" + ("." * dots if dots else "")
    draw.text((4, _LIST_TITLE_Y), title, font=font, fill=theme.FG)
    draw.line([(0, _LIST_LINE_Y), (WIDTH, _LIST_LINE_Y)], fill=theme.SEP)

    networks = state.networks
    n = len(networks)
    total = n + 2

    # Visible-window scroll: keep the cursor row on screen.
    footer_top = HEIGHT - theme.FOOTER_HEIGHT
    visible = max(1, (footer_top - _LIST_TOP) // _ROW_H)
    start = 0
    if state.cursor >= visible:
        start = state.cursor - visible + 1

    for i in range(start, min(total, start + visible)):
        y = _LIST_TOP + (i - start) * _ROW_H
        selected = (i == state.cursor)
        if selected:
            widgets.selection_band(draw, y, _ROW_H)
        fg = theme.SEL_FG if selected else theme.FG
        dim = theme.SEL_DIM if selected else theme.DIM
        if i < n:
            net = networks[i]
            draw.text((_GLYPH_X, y + 2), signal_glyph(net.signal), font=font, fill=fg)
            ssid = net.ssid
            if len(ssid) > _SSID_MAX_CHARS:
                ssid = ssid[: _SSID_MAX_CHARS - 1] + "…"
            draw.text((_SSID_X, y + 2), ssid, font=font, fill=fg)
            if net.secured:
                _draw_lock(draw, _LOCK_X, y + 3, dim)
            if net.active:
                cr = 3
                cx, cy = _DOT_X, y + _ROW_H // 2
                draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=theme.OK_DOT)
        elif i == n:
            draw.text((_GLYPH_X, y + 2), "+ Other network…", font=font, fill=fg)
        else:
            draw.text((_GLYPH_X, y + 2), "↻ Rescan", font=font, fill=fg)

    widgets.footer(draw, "OK connect  holdOK edit  holdESC forget")
    return rgba.convert("RGB")


# ----------------------------------------------------------------------
# Status screen (connecting / connected / failed)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class WifiStatusState:
    phase: str               # "connecting" | "connected" | "failed"
    ssid: str
    ip: Optional[str] = None
    outcome: Optional[ConnectOutcome] = None
    detail: Optional[str] = None


# Friendly per-outcome failure message.
_FAILURE_MESSAGE = {
    ConnectOutcome.BAD_AUTH: "Wrong password",
    ConnectOutcome.NOT_FOUND: "Network not in range",
    ConnectOutcome.TIMEOUT: "Timed out (30 s)",
    ConnectOutcome.FAILED: "Couldn't connect",
}


def _draw_centered(draw, text: str, y: int, font, color) -> None:
    tw = int(draw.textlength(text, font=font))
    draw.text(((WIDTH - tw) // 2, y), text, font=font, fill=color)


def render_wifi_status(
    base: Image.Image, state: WifiStatusState, *, dots: Optional[int],
) -> Image.Image:
    """Compose the Wi-Fi status screen over `base`. Returns RGB 320x240."""
    rgba, draw = widgets.new_overlay_canvas(base)
    font = fonts.mono(_BODY_PT)

    if state.phase == "connecting":
        label = "Connecting" + ("." * dots if dots else "")
        _draw_centered(draw, label, 90, font, theme.FG)
        _draw_centered(draw, state.ssid, 120, font, theme.DIM)
    elif state.phase == "connected":
        _draw_centered(draw, "Connected", 70, font, theme.OK_DOT)
        _draw_centered(draw, f"SSID  {state.ssid}", 110, font, theme.FG)
        if state.ip:
            _draw_centered(draw, f"IP    {state.ip}", 130, font, theme.FG)
        widgets.footer(draw, "OK / ESC  back")
    else:  # failed
        _draw_centered(draw, "Couldn't connect", 70, font, theme.ERR)
        msg = _FAILURE_MESSAGE.get(state.outcome or ConnectOutcome.FAILED, "Couldn't connect")
        _draw_centered(draw, msg, 110, font, theme.FG)
        if state.detail:
            detail = state.detail
            if len(detail) > 40:
                detail = detail[:39] + "…"
            _draw_centered(draw, detail, 130, font, theme.DIM)
        widgets.footer(draw, "OK retry   ESC back")

    return rgba.convert("RGB")
