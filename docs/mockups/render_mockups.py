#!/usr/bin/env python3
"""Render UI mockups at 320x240 (native TFT resolution).

Run from the repo root:
    .venv/bin/python docs/mockups/render_mockups.py

Outputs PNGs into docs/mockups/ alongside this script. Re-run after any
edit to keep the documented design in sync with the spec.

Status: las pantallas portadas a `fp_lapse.ui` se renderizan llamando al
código productivo (fuente única de verdad). Las que aún no — overlay y
manage_menu — siguen con las primitivas locales hasta que se porten.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Permite importar `fp_lapse` desde `src/` sin instalar el paquete.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1] / "src"))

from datetime import date as _date_t, time as _time_t  # noqa: E402

from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import EngineState  # noqa: E402
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402
from fp_lapse.ui.edit_screen import EditScreen, EditState  # noqa: E402
from fp_lapse.ui.bracket_screen import (  # noqa: E402
    BracketGenState,
    render_bracket_gen,
)
from fp_lapse.ui.main_screen import MainScreen, UIState  # noqa: E402
from fp_lapse.ui.manage_menu import ManageMenuState  # noqa: E402
from fp_lapse.ui.manage_menu import render_manage_menu as _render_manage_menu  # noqa: E402
from fp_lapse.ui.overlays import (  # noqa: E402
    poweroff_confirm,
    render_overlay,
    save_confirm,
    stop_confirm,
)
from fp_lapse.ui.shutdown_screen import render_powering_off  # noqa: E402
from fp_lapse.ui.picker_datetime import (  # noqa: E402
    DateTimePickerInteraction,
    render_datetime_picker,
)
from fp_lapse.ui.schedule_indicator import ScheduleIndicator  # noqa: E402
from fp_lapse.ui.time_setup_menu import (  # noqa: E402
    TimeSetupMenuState,
    render_time_setup_menu,
)
from fp_lapse.net.nmcli import (  # noqa: E402
    ConnectOutcome,
    WifiNetwork,
)
from fp_lapse.ui.keyboard import (  # noqa: E402
    KeyboardState,
    render_keyboard,
)
from fp_lapse.ui.overlays import wifi_forget_confirm  # noqa: E402
from fp_lapse.ui.wifi_screen import (  # noqa: E402
    WifiListState,
    WifiStatusState,
    render_wifi_list,
    render_wifi_status,
)

W, H = 320, 240

# Palette tuned for high contrast on a small RGB565 TFT
BG       = (10, 14, 20)
FG       = (235, 235, 230)
DIM      = (130, 130, 135)
SEP      = (45, 50, 60)

SEL_BG   = (215, 215, 215)
SEL_FG   = (10, 10, 14)
SEL_DIM  = (90, 90, 95)
SEL_BAR  = (255, 200, 0)

RUN_DOT  = (240, 60, 60)
OK_DOT   = (90, 200, 90)
WARN     = (240, 200, 60)
ERR      = (240, 60, 60)

OVERLAY_SHADE = (0, 0, 0, 170)
DIALOG_BG = (28, 32, 42)
DIALOG_BORDER = (90, 95, 110)


# Fonts ---------------------------------------------------------------
def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


MENLO = "/System/Library/Fonts/Menlo.ttc"
HELV = "/System/Library/Fonts/Helvetica.ttc"

F_BODY  = _font(MENLO, 11)
F_BOLD  = _font("/System/Library/Fonts/Menlo.ttc", 11)  # Menlo doesn't ship bold via name; use same
F_TITLE = _font(HELV, 14)
F_SMALL = _font(MENLO, 9)


# Helpers -------------------------------------------------------------
def _text_w(d, s, font):
    try:
        return d.textlength(s, font=font)
    except Exception:
        return font.getbbox(s)[2]


def status_bar(d, *, time_str="18:42:07", cam_ok=True,
               skips=0, show_skips=True):
    """Top status bar, ~18px tall.

    Battery is intentionally not shown: sigma-ptpy does not expose the
    fp's battery state. See reference.md §7.1 note and §10.
    """
    d.text((4, 2), time_str, font=F_BODY, fill=FG)
    d.text((78, 2), "fp", font=F_BODY, fill=FG)
    cx, cy, cr = 100, 8, 3
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr],
              fill=OK_DOT if cam_ok else ERR)
    if show_skips:
        s = f"SKIPS {skips}"
        sw = _text_w(d, s, F_BODY)
        col = WARN if skips > 0 else FG
        d.text((W - 6 - sw, 2), s, font=F_BODY, fill=col)
    d.line([(0, 18), (W, 18)], fill=SEP)


def footer(d, hint):
    y = H - 16
    d.line([(0, y), (W, y)], fill=SEP)
    d.text((4, y + 3), hint, font=F_BODY, fill=DIM)


def base_canvas():
    return Image.new("RGB", (W, H), BG)


# Shot row rendering --------------------------------------------------
COL_IDX   = 14   # bracket index (single digit)
COL_SHUT  = 32   # leave ~2 char gap after the index
COL_ISO   = 112
COL_APER  = 208


def draw_shot_row(d, y, *, idx=None, shutter="—", iso="ISO —", aper="f/—",
                  on_selected=False):
    text_col = SEL_FG if on_selected else FG
    dim_col = SEL_DIM if on_selected else DIM
    if idx is not None:
        d.text((COL_IDX, y), str(idx), font=F_BODY, fill=dim_col)
    is_dash = shutter == "—" or shutter.startswith("ISO —") or shutter == "f/—"
    d.text((COL_SHUT, y), shutter, font=F_BODY,
           fill=dim_col if shutter == "—" else text_col)
    d.text((COL_ISO,  y), iso, font=F_BODY,
           fill=dim_col if iso == "ISO —" else text_col)
    d.text((COL_APER, y), aper, font=F_BODY,
           fill=dim_col if aper == "f/—" else text_col)


def draw_header_row(d, y, name, summary, *, on_selected=False, running=False):
    text_col = SEL_FG if on_selected else FG
    x = 8
    if running:
        # red dot just before the name
        rx, ry, rr = x + 2, y + 6, 3
        d.ellipse([rx - rr, ry - rr, rx + rr, ry + rr], fill=RUN_DOT)
        x = rx + rr + 4
    d.text((x, y), name, font=F_BODY, fill=text_col)
    sw = _text_w(d, summary, F_BODY)
    d.text((W - 6 - sw, y), summary, font=F_BODY, fill=text_col)


def selection_band(d, y0, height):
    """Draw the selected-row band (full width) + left yellow bar."""
    d.rectangle([0, y0, W - 1, y0 + height - 1], fill=SEL_BG)
    d.rectangle([0, y0, 3, y0 + height - 1], fill=SEL_BAR)


# Sample data ---------------------------------------------------------
CONFIGS = [
    ("Partial", "10 s · 1 shot", [
        (None, "1/1000", "ISO 200", "f/—"),
    ]),
    ("Totality", "5 s · 5 shots", [
        (1, "1/500", "ISO 400", "f/—"),
        (2, "1/125", "ISO 400", "f/—"),
        (3, "1/30",  "ISO 400", "f/—"),
        (4, "1/8",   "ISO 400", "f/—"),
        (5, "2 s",   "ISO auto", "f/—"),
    ]),
    ("Free daytime", "30 s · 1 shot", [
        (None, "—", "ISO —", "f/—"),
    ]),
]


def config_height(shots, running=False):
    n = len(shots)
    h = 13 + 12 * n + 4
    if running:
        h += 12  # extra line for "taken N   next in Xs"
    return h


def draw_config_block(d, y, name, summary, shots, *, selected=False,
                      running=False, taken=None, next_in=None):
    h = config_height(shots, running=running)
    if selected:
        selection_band(d, y, h - 4)
    draw_header_row(d, y, name, summary,
                    on_selected=selected, running=running)
    yy = y + 12
    if running:
        sub_col = SEL_DIM if selected else DIM
        sub = f"taken {taken}   next in {next_in}"
        d.text((22, yy), sub, font=F_BODY, fill=sub_col)
        yy += 12
    for sh in shots:
        idx, shutter, iso, aper = sh
        draw_shot_row(d, yy, idx=idx, shutter=shutter, iso=iso, aper=aper,
                      on_selected=selected)
        yy += 12
    return y + h


# Screens -------------------------------------------------------------
# Main screen fixtures match `tests/test_ui_main_screen.py` 1:1 — the
# tests assert byte equality against the PNGs we generate here, so any
# drift breaks them. Keep these aligned.
_PARTIAL = TimelapseConfig(
    name="Partial", interval_s=10.0,
    shots=(Shot(shutter=1 / 1000, iso=200, aperture=None),),
)
_TOTALITY = TimelapseConfig(
    name="Totality", interval_s=5.0,
    shots=(
        Shot(shutter=1 / 500, iso=400, aperture=None),
        Shot(shutter=1 / 125, iso=400, aperture=None),
        Shot(shutter=1 / 30,  iso=400, aperture=None),
        Shot(shutter=1 / 8,   iso=400, aperture=None),
        Shot(shutter=2.0,     iso=1600, aperture=None),
    ),
)
# Auto mode: 1 shot per interval, camera meters.
_FREE = TimelapseConfig(name="Free daytime", interval_s=30.0, shots=())


def render_main_idle():
    """IDLE main screen rendered via production code."""
    return MainScreen().render(UIState(
        configs=(_PARTIAL, _TOTALITY, _FREE),
        cursor=0,
        engine_state=EngineState.IDLE,
        active_config_name=None,
        shots_taken=0,
        seconds_to_next_shot=None,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
    ))


def render_main_running_on_running():
    """RUNNING, cursor sits on the running config (Totality)."""
    return MainScreen().render(UIState(
        configs=(_TOTALITY, _PARTIAL),
        cursor=0,
        engine_state=EngineState.RUNNING,
        active_config_name="Totality",
        shots_taken=142,
        seconds_to_next_shot=4.3,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
    ))


def render_main_running_off_running():
    """RUNNING, cursor sits on a different config (Partial)."""
    return MainScreen().render(UIState(
        configs=(_TOTALITY, _PARTIAL),
        cursor=1,
        engine_state=EngineState.RUNNING,
        active_config_name="Totality",
        shots_taken=142,
        seconds_to_next_shot=4.3,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
    ))


def render_edit():
    """Pantalla de edición — renderizada por el código productivo."""
    cfg = TimelapseConfig(
        name="Totality",
        interval_s=5.0,
        shots=(
            Shot(shutter=1 / 500, iso=400, aperture=None),
            Shot(shutter=1 / 125, iso=400, aperture=None),
            Shot(shutter=1 / 30, iso=400, aperture=None),
            Shot(shutter=1 / 8, iso=400, aperture=None),
            Shot(shutter=2.0, iso="auto", aperture=None),
        ),
    )
    # field_cursor=3 = "#1 shutter" (campos: name, interval, bracket size, #1 shutter, …)
    state = EditState(cfg=cfg, field_cursor=3, scroll_offset=0)
    return EditScreen().render(state)


def render_bracket_gen_preview():
    """Generator screen — populated preview, no drops (PRD mockup).

    Reference 1/500·ISO 400·f/8, darkest, 1 EV, 5 shots, iso1=400,
    iso2=off → a clean ISO-400 ladder. Cursor on `iso 2`.
    """
    state = BracketGenState(
        reference=Shot(1 / 500, 400, 8.0),
        brightest=False, ev_step=1, n=5, iso1=400, iso2=None,
        field_cursor=7, config_name="Totality",
    )
    return render_bracket_gen(state)


def render_bracket_gen_dropped():
    """Generator screen — dropped-shots state (PRD mockup).

    Reference 1/1000·ISO 200·f/5.6, brightest, 3 EV, 5 shots, iso1=100,
    iso2=off → rungs 2–4 drop (too fast even at ISO 100). Cursor on
    `direction`.
    """
    state = BracketGenState(
        reference=Shot(1 / 1000, 200, 5.6),
        brightest=True, ev_step=3, n=5, iso1=100, iso2=None,
        field_cursor=3, config_name="Corona",
    )
    return render_bracket_gen(state)


def render_overlay_stop():
    """Confirmation overlay on top of running main screen — productive code."""
    return render_overlay(render_main_running_on_running(), stop_confirm())


def render_overlay_poweroff():
    """§7.8 — `Power off?` overlay on top of the idle main screen."""
    return render_overlay(render_main_idle(), poweroff_confirm())


def render_shutdown_powering_off():
    """§7.8 — single `POWERING OFF…` screen with LED hint."""
    return render_powering_off()


def render_manage_menu():
    """Manage menu overlay opened via long-press OK on Totality — productive code."""
    return _render_manage_menu(
        render_main_idle(),
        ManageMenuState(config_name="Totality", cursor=0),
    )


# ----------------------------------------------------------------------
# prd2.md §6 — schedule UI mockups
# ----------------------------------------------------------------------


def _render_main_idle_with_schedule(
    state: ScheduleIndicator, *, disabled: bool = False,
):
    """Main IDLE rendered with the given schedule indicator state."""
    return MainScreen().render(UIState(
        configs=(_PARTIAL, _TOTALITY, _FREE),
        cursor=0,
        engine_state=EngineState.IDLE,
        active_config_name=None,
        shots_taken=0,
        seconds_to_next_shot=None,
        skips=0,
        camera_connected=True,
        wall_clock_str="18:42:07",
        schedule_state=state,
        schedule_disabled=disabled,
    ))


def render_main_idle_schedule_off():
    return _render_main_idle_with_schedule(ScheduleIndicator.OFF)


def render_main_idle_schedule_red():
    return _render_main_idle_with_schedule(ScheduleIndicator.RED)


def render_main_idle_schedule_green():
    return _render_main_idle_with_schedule(ScheduleIndicator.GREEN)


def render_main_idle_schedule_yellow():
    return _render_main_idle_with_schedule(ScheduleIndicator.YELLOW)


def render_main_idle_schedule_disabled():
    """§6 addendum: schedule is disabled. Clock glyph carries a
    diagonal strikethrough while the dot still shows the would-be
    color (GREEN here — the operator turned scheduling off after a
    successful first sync)."""
    return _render_main_idle_with_schedule(
        ScheduleIndicator.GREEN, disabled=True,
    )


def render_edit_with_schedule():
    """Edit screen with START and END moments set, cursor on `start`."""
    cfg = TimelapseConfig(
        name="Totality",
        interval_s=5.0,
        shots=(
            Shot(shutter=1 / 500, iso=400, aperture=None),
            Shot(shutter=1 / 125, iso=400, aperture=None),
            Shot(shutter=1 / 30, iso=400, aperture=None),
            Shot(shutter=1 / 8, iso=400, aperture=None),
            Shot(shutter=2.0, iso=1600, aperture=None),
        ),
        start=ScheduledMoment(
            time=_time_t(11, 33, 23),
            date=_date_t(2026, 8, 12),
        ),
        end=ScheduledMoment(
            time=_time_t(11, 36, 9),
            date=_date_t(2026, 8, 12),
        ),
    )
    # field_cursor=3 = "start" (after name/interval/shots).
    return EditScreen().render(EditState(cfg=cfg, field_cursor=3, scroll_offset=0))


def render_picker_datetime():
    """Picker in DATE_TIME mode pre-populated with the eclipse start."""
    base = render_edit_with_schedule()
    picker = DateTimePickerInteraction(
        target_field="start",
        initial_value=ScheduledMoment(
            time=_time_t(11, 33, 23),
            date=_date_t(2026, 8, 12),
        ),
    )
    # Place the cursor on the minutes-tens digit (cell index 10 in
    # the DATE_TIME layout YYYY-MM-DD HH:MM:SS — see picker_datetime
    # `_layout_template`).
    for _ in range(10):
        from fp_lapse.buttons.iface import ButtonId  # local import — small CLI
        picker.on_press(ButtonId.RIGHT)
    return render_datetime_picker(
        base, picker.state, title="Edit · Totality · start",
    )


def render_overlay_save_with_warning():
    """Save overlay with the past-date warning body line."""
    cfg = TimelapseConfig(
        name="Totality", interval_s=5.0,
        shots=(Shot(shutter=1 / 500, iso=400, aperture=None),),
        start=ScheduledMoment(
            time=_time_t(11, 33, 23),
            date=_date_t(2020, 8, 12),   # well in the past
        ),
    )
    base = EditScreen().render(EditState(cfg=cfg, field_cursor=3, scroll_offset=0))
    return render_overlay(
        base,
        save_confirm(warning="Start date past — won't fire"),
    )


def render_main_idle_time_setup_menu():
    """TIME SETUP menu overlay over a schedule-GREEN main screen."""
    base = render_main_idle_schedule_green()
    return render_time_setup_menu(base, TimeSetupMenuState(cursor=0))


def render_main_idle_with_scheduled_configs():
    """Addendum E: main IDLE view showing per-config start/end lines.

    Three configs to exercise every schedule-line shape:
      - `Partial-1`: one-shot start only.
      - `Totality`: one-shot start + end on the same day (collapsed
        single-line rendering with the date shown once).
      - `Sunrise loop`: daily moments (time-only, no date).
    """
    from datetime import date as _date, time as _time
    eclipse_day = _date(2026, 8, 12)
    p1 = TimelapseConfig(
        name="Partial-1", interval_s=10.0,
        shots=(Shot(shutter=1 / 1000, iso=200, aperture=None),),
        start=ScheduledMoment(time=_time(10, 0, 0), date=eclipse_day),
    )
    tot = TimelapseConfig(
        name="Totality", interval_s=5.0,
        shots=(
            Shot(shutter=1 / 500, iso=400, aperture=None),
            Shot(shutter=1 / 30, iso=400, aperture=None),
        ),
        start=ScheduledMoment(time=_time(11, 33, 23), date=eclipse_day),
        end=ScheduledMoment(time=_time(11, 36, 9), date=eclipse_day),
    )
    daily = TimelapseConfig(
        name="Sunrise loop", interval_s=30.0, shots=(),
        start=ScheduledMoment(time=_time(7, 0, 0)),
        end=ScheduledMoment(time=_time(19, 0, 0)),
    )
    return MainScreen().render(UIState(
        configs=(p1, tot, daily),
        cursor=1,
        engine_state=EngineState.IDLE,
        active_config_name=None,
        shots_taken=0,
        seconds_to_next_shot=None,
        skips=0,
        camera_connected=True,
        wall_clock_str="09:55:12",
        schedule_state=ScheduleIndicator.GREEN,
    ))


# ----------------------------------------------------------------------
# wifi-manual-config — SETTINGS menu, Wi-Fi list, keyboard, status
# ----------------------------------------------------------------------

_WIFI_NETS = (
    WifiNetwork("MyHomeWiFi", 72, secured=True, active=True, saved=True),
    WifiNetwork("Guest_Network", 55, secured=True, active=False, saved=False),
    WifiNetwork("CoffeeShop", 48, secured=False, active=False, saved=False),
    WifiNetwork("Router_5G", 30, secured=True, active=False, saved=False),
    WifiNetwork("Cabin_5G", 12, secured=True, active=False, saved=False),
)


def render_settings_menu():
    """The flat SETTINGS menu (3 items) over the idle main screen."""
    return render_time_setup_menu(render_main_idle(), TimeSetupMenuState(cursor=0))


def render_wifi_list_mockup():
    """Wi-Fi network list with the active/saved network highlighted."""
    return render_wifi_list(
        render_main_idle(),
        WifiListState(_WIFI_NETS, cursor=0, scanning=False),
        dots=None,
    )


def render_keyboard_password():
    """Virtual keyboard, abc layer, password target (masked, mask key)."""
    state = KeyboardState(
        target="password", text="hunter7", layer="abc", masked=True,
        cursor_row=3, cursor_col=3,   # mask key under the cursor
    )
    return render_keyboard(render_main_idle(), state, title="Wi-Fi password")


def render_keyboard_ssid():
    """Virtual keyboard, abc layer, ssid target (no mask key)."""
    state = KeyboardState(
        target="ssid", text="Hidden", layer="abc", masked=False,
        cursor_row=0, cursor_col=0,
    )
    return render_keyboard(render_main_idle(), state, title="Network name")


def render_keyboard_config_name():
    """Virtual keyboard, abc layer, config-name target (no mask key)."""
    state = KeyboardState(
        target="config_name", text="Totality", layer="abc", masked=False,
        cursor_row=0, cursor_col=0,
    )
    return render_keyboard(render_main_idle(), state, title="Config name")


def render_wifi_connecting():
    return render_wifi_status(
        render_main_idle(),
        WifiStatusState(phase="connecting", ssid="MyHomeWiFi"),
        dots=2,
    )


def render_wifi_connected():
    return render_wifi_status(
        render_main_idle(),
        WifiStatusState(phase="connected", ssid="MyHomeWiFi", ip="192.168.1.42"),
        dots=None,
    )


def render_wifi_failed():
    return render_wifi_status(
        render_main_idle(),
        WifiStatusState(
            phase="failed", ssid="MyHomeWiFi",
            outcome=ConnectOutcome.BAD_AUTH,
            detail="secrets were required",
        ),
        dots=None,
    )


def render_wifi_forget_confirm_mockup():
    base = render_wifi_list_mockup()
    return render_overlay(base, wifi_forget_confirm("MyHomeWiFi"))


# Main ----------------------------------------------------------------
OUT = Path(__file__).parent


def save(img, name):
    img.save(OUT / f"{name}.png")
    print(f"  wrote {name}.png")


def main():
    print("Rendering UI mockups...")
    save(render_main_idle(),                "01_main_idle")
    save(render_main_running_on_running(),  "02_main_running_cursor_on_running")
    save(render_main_running_off_running(), "03_main_running_cursor_elsewhere")
    save(render_edit(),                     "04_edit")
    save(render_overlay_stop(),             "05_overlay_stop_confirm")
    save(render_manage_menu(),              "06_manage_menu")
    # prd2.md §6 — schedule UI mockups.
    save(render_main_idle_schedule_off(),     "07_main_idle_schedule_off")
    save(render_main_idle_schedule_red(),     "08_main_idle_schedule_red")
    save(render_main_idle_schedule_green(),   "09_main_idle_schedule_green")
    save(render_main_idle_schedule_yellow(),  "10_main_idle_schedule_yellow")
    save(render_edit_with_schedule(),         "11_edit_with_schedule")
    save(render_picker_datetime(),            "12_picker_datetime")
    save(render_overlay_save_with_warning(),  "13_overlay_save_with_warning")
    save(render_main_idle_time_setup_menu(),  "14_main_idle_time_setup_menu")
    save(render_main_idle_with_scheduled_configs(),
         "15_main_idle_with_scheduled_configs")
    # §7.8 — safe shutdown.
    save(render_overlay_poweroff(),       "16_overlay_poweroff")
    save(render_shutdown_powering_off(),  "17_powering_off")
    # §6 addendum — schedule disabled with strikethrough.
    save(render_main_idle_schedule_disabled(),
         "19_main_idle_schedule_disabled")
    # wifi-manual-config — SETTINGS menu + Wi-Fi flow.
    save(render_settings_menu(),                "20_settings_menu")
    save(render_wifi_list_mockup(),             "21_wifi_list")
    save(render_keyboard_password(),            "22_keyboard_password_abc")
    save(render_keyboard_ssid(),                "23_keyboard_ssid")
    save(render_wifi_connecting(),              "24_wifi_connecting")
    save(render_wifi_connected(),               "25_wifi_connected")
    save(render_wifi_failed(),                  "26_wifi_failed")
    save(render_wifi_forget_confirm_mockup(),   "27_wifi_forget_confirm")
    # semiauto-bracketing — generator sub-screen.
    save(render_bracket_gen_preview(),          "28_bracket_gen_preview")
    save(render_bracket_gen_dropped(),          "29_bracket_gen_dropped")
    # Config-name on-screen keyboard.
    save(render_keyboard_config_name(),         "30_keyboard_config_name")
    print("Done.")


if __name__ == "__main__":
    main()
