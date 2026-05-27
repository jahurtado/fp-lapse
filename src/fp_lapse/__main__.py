"""Entry point — `python -m fp_lapse`.

Event-driven main loop:

  - **Engine** runs in its own thread (`EngineScheduler`). It blocks on
    a kernel sleep until the next grid mark and fires `engine.tick()`
    exactly when due, immune to UI cadence and rendering cost.
  - **Button events** fire `app.on_press / on_release` directly from
    the GPIO callback thread (gpiozero) or the Tk thread (Mac dev)
    under `app.lock`. No polling queue.
  - **OK long-press** is detected by a `threading.Timer(3 s)` armed on
    OK press and cancelled on release.
  - **UI** runs on the main thread; it blocks on `dirty_event` with a
    250 ms timeout, so it re-renders both on every state change and at
    ~4 Hz when idle (so the "next in X.Xs" counter keeps ticking).

Platform detection:

  - macOS (`sys.platform == "darwin"`) or `FP_LAPSE_MOCK=1`: TkDisplay
    + TkButtonPanel + MockCamera. For Mac dev without hardware.
  - Otherwise (assume Pi/Linux with the TFT and GPIO connected):
    Framebuffer + GpioButtonPanel + SigmaFpCamera over USB.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

from .app import App
from .buttons.iface import ButtonId
from .camera_health import CameraHealth
from .config import CONFIGS_FILE
from .configs import ConfigStore
from .engine import Engine
from .engine_scheduler import EngineScheduler
from .logging_setup import setup_logging

logger = logging.getLogger("fp_lapse")


# How often the UI thread refreshes when nothing else has set the
# dirty event. 250 ms is fine-grained enough for the "next in X.Xs"
# countdown to show tenths, and at the same time keeps CPU usage near
# zero during idle stretches.
UI_REFRESH_TIMEOUT_S: float = 0.250

# Short interval between Tk pump cycles while inside the UI wait.
# Only matters on Mac dev (where Tk callbacks need pumping); on the
# Pi `_pump_tk()` is a no-op. Chosen small enough that Tk button
# latency stays under ~50 ms.
TK_PUMP_INTERVAL_S: float = 0.050

# OK held this long opens the manage menu (§7.1 / §7.5).
LONG_PRESS_S: float = 3.0


def _use_mock() -> bool:
    if os.environ.get("FP_LAPSE_MOCK") == "1":
        return True
    return sys.platform == "darwin"


def _build_display():
    if _use_mock():
        from .display.mock import TkDisplay
        return TkDisplay()
    from .display.framebuffer import Framebuffer
    return Framebuffer()


def _build_buttons():
    if _use_mock():
        from .buttons.mock import TkButtonPanel
        return TkButtonPanel()
    from .buttons.gpio import GpioButtonPanel
    return GpioButtonPanel.create()


def _build_camera():
    """Build the camera as a `CameraProxy`.

    The proxy detects the attached camera by USB VID/PID (or the
    `FP_LAPSE_CAMERA` / `FP_LAPSE_MOCK` override) and holds the matching
    adapter, swapping it at runtime when the body changes (hot-swap). It is
    the single stable reference handed to the engine, app and health thread.

    The initial `connect()` is wrapped in try/except so the app still boots
    when no camera is attached — the camera-health thread keeps retrying,
    and plugging a camera in later (or swapping one for another) is picked
    up automatically.
    """
    from .camera.proxy import CameraProxy
    cam = CameraProxy()
    try:
        cam.connect()
    except Exception as e:
        logger.warning("could not connect to camera: %s", e)
    return cam


def _pump_tk() -> None:
    if _use_mock():
        from . import _tk_root
        _tk_root.pump()


class ButtonRouter:
    """Wires the `ButtonPanel` callbacks to the `App` handlers.

    Responsibilities:

    - Forward press / release to `app.on_press` / `app.on_release`.
    - Arm a `threading.Timer` on OK press and cancel it on release;
      when the timer fires (3 s elapsed), call `app.on_long_press`.
    - Set `dirty_event` after every dispatch so the UI thread
      re-renders promptly.

    All entry points run on whatever thread gpiozero / Tk uses for the
    callback. The handlers themselves take `app.lock` internally.
    """

    def __init__(self, *, app: App, dirty_event: threading.Event) -> None:
        self._app = app
        self._dirty = dirty_event
        self._lp_lock = threading.Lock()
        self._lp_timer: Optional[threading.Timer] = None

    def attach(self, panel) -> None:
        for bid in ButtonId:
            panel.on_press(bid, lambda b=bid: self.on_press(b))
            panel.on_release(bid, lambda b=bid: self.on_release(b))

    # GPIO/Tk thread → app
    def on_press(self, bid: ButtonId) -> None:
        try:
            self._app.on_press(bid)
        except Exception:
            logger.exception("on_press(%s) raised", bid.name)
        if bid == ButtonId.OK:
            self._arm_long_press()
        self._dirty.set()

    def on_release(self, bid: ButtonId) -> None:
        if bid == ButtonId.OK:
            self._cancel_long_press()
        try:
            self._app.on_release(bid)
        except Exception:
            logger.exception("on_release(%s) raised", bid.name)
        self._dirty.set()

    def _arm_long_press(self) -> None:
        with self._lp_lock:
            if self._lp_timer is not None:
                self._lp_timer.cancel()
            self._lp_timer = threading.Timer(LONG_PRESS_S, self._fire_long_press)
            self._lp_timer.daemon = True
            self._lp_timer.start()

    def _cancel_long_press(self) -> None:
        with self._lp_lock:
            if self._lp_timer is not None:
                self._lp_timer.cancel()
                self._lp_timer = None

    def _fire_long_press(self) -> None:
        try:
            self._app.on_long_press(ButtonId.OK)
        except Exception:
            logger.exception("on_long_press(OK) raised")
        self._dirty.set()


def _wait_for_dirty(
    dirty_event: threading.Event, total_timeout: float
) -> bool:
    """Wait up to `total_timeout`, pumping Tk in small chunks.

    Returns True if the event fired during the wait, False on timeout.
    On the Pi (`_pump_tk` no-op) this is just a chunked sleep — the
    chunks let `signal.SIGTERM` reach the main thread promptly. On
    Mac, the chunks let Tk callbacks dispatch every ~50 ms.
    """
    end = time.monotonic() + total_timeout
    while True:
        _pump_tk()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        chunk = min(TK_PUMP_INTERVAL_S, remaining)
        if dirty_event.wait(timeout=chunk):
            return True


def _install_signal_handlers(
    shutdown_event: threading.Event,
    dirty_event: threading.Event,
) -> None:
    def handler(signum, frame):
        logger.info("signal %d received — shutting down", signum)
        shutdown_event.set()
        # Wake the UI thread out of any current wait so the loop exits.
        dirty_event.set()
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> int:
    setup_logging()
    logger.info("fp-lapse starting (mock=%s)", _use_mock())

    display = _build_display()
    buttons = _build_buttons()
    camera = _build_camera()

    store = ConfigStore(CONFIGS_FILE)
    engine = Engine(camera)

    dirty_event = threading.Event()
    shutdown_event = threading.Event()
    _install_signal_handlers(shutdown_event, dirty_event)

    scheduler = EngineScheduler(engine, dirty_event=dirty_event)
    app = App(scheduler=scheduler, store=store, camera=camera)
    logger.info("loaded %d configs from %s", len(app.configs), CONFIGS_FILE)
    if app._configs_reset:  # type: ignore[attr-defined]
        logger.warning("configs.json was reset due to corruption")

    router = ButtonRouter(app=app, dirty_event=dirty_event)
    router.attach(buttons)

    if os.environ.get("FP_LAPSE_CONTROL") == "1":
        from .control_server import ControlServer, _app_snapshot, DEFAULT_PORT
        port = int(os.environ.get("FP_LAPSE_CONTROL_PORT", DEFAULT_PORT))
        ControlServer(
            inject_press=router.on_press,
            inject_release=router.on_release,
            snapshot=lambda: _app_snapshot(app),
            render_frame=app.render,
            port=port,
        ).start()

    scheduler.start()
    # Camera health watchdog: handles startup-without-camera and
    # mid-session USB disconnects. On the Mac mock path it's a no-op
    # (MockCamera.is_connected() stays True) so we leave it always
    # wired in.
    camera_health = CameraHealth(camera, dirty_event=dirty_event)
    camera_health.start()
    logger.info("UI loop started (dirty timeout %.0f ms)", UI_REFRESH_TIMEOUT_S * 1000)
    try:
        # Initial paint so the screen isn't black until the first event.
        display.blit(app.render())
        while not shutdown_event.is_set():
            _wait_for_dirty(dirty_event, UI_REFRESH_TIMEOUT_S)
            dirty_event.clear()
            if shutdown_event.is_set():
                break
            frame = app.render()
            display.blit(frame)
    finally:
        logger.info("shutting down")
        camera_health.shutdown(timeout=2.0)
        scheduler.shutdown(timeout=2.0)
        for closer in (display, buttons):
            try:
                closer.close()
            except Exception:
                pass
        try:
            camera.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
