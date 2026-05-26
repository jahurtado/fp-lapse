"""Background thread that keeps the camera connection alive.

The Sigma fp over USB is a finicky link: a powerbank momentarily
brown-outs, the cable wobbles, the body auto-shuts after a long
exposure run, or the user simply needs to swap a battery. With the
old polling main loop a single `USBError` in `shoot()` would log and
move on, but nothing tried to bring the connection back. The user
had to restart the service.

This thread closes that gap. On a 5 s cadence (configurable):

- If `camera.is_connected()` is False, call `camera.connect()`.
- If `is_connected()` is True, **probe** with a lightweight PTP call
  (`info()` — one roundtrip). Without the probe, `is_connected()`
  stays True after the cable is pulled because no PTP traffic flowed
  to discover the dead bus. With the probe, a silent disconnect is
  detected within one tick.

Either branch sets the optional `dirty_event` on a state change so
the UI re-renders (camera indicator etc.) immediately.

Lock interaction: `camera.connect()` / `camera.info()` acquire the
adapter's own internal lock (which also guards `shoot()` from the
scheduler thread). If a shoot is in flight, this thread blocks
until it finishes — fine, the 5 s cadence has plenty of slack.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


DEFAULT_INTERVAL_S: float = 5.0


class CameraHealth:
    def __init__(
        self,
        camera,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        dirty_event: Optional[threading.Event] = None,
    ) -> None:
        self._camera = camera
        self._interval = interval_s
        self._dirty = dirty_event
        self._shutdown = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="camera-health", daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        self._shutdown.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        logger.info("camera-health: thread started (interval=%.1fs)", self._interval)
        while not self._shutdown.is_set():
            self._tick_once()
            # Use wait() rather than sleep() so shutdown() is responsive.
            self._shutdown.wait(timeout=self._interval)
        logger.info("camera-health: thread exiting")

    def _tick_once(self) -> None:
        try:
            connected = self._camera.is_connected()
        except Exception:
            logger.exception("camera-health: is_connected() raised")
            return

        if connected:
            # Active probe. The `SigmaFpCamera.info()` adapter wraps
            # any transport error with `_mark_disconnected()` which
            # resets `_cam = None` and re-raises `CameraNotConnected`.
            try:
                self._camera.info()
                return
            except Exception as e:
                # We treat ANY probe failure as "lost the camera". The
                # adapter has already done the bookkeeping
                # (`is_connected()` now returns False); we log and
                # fall through to the reconnect branch.
                logger.warning("camera-health: probe failed: %s", e)
                if self._dirty is not None:
                    self._dirty.set()
                if self._camera.is_connected():
                    # Adapter still thinks it's connected — paranoia
                    # safety net. Don't try to reconnect on top of
                    # a possibly-still-good session.
                    return

        try:
            self._camera.connect()
        except Exception as e:
            logger.warning("camera-health: reconnect failed: %s", e)
            return
        if self._camera.is_connected():
            logger.info("camera-health: reconnected")
            if self._dirty is not None:
                self._dirty.set()
