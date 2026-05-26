"""Loopback HTTP control surface for autonomous testing.

Opt-in via `FP_LAPSE_CONTROL=1`. Binds to `127.0.0.1` only — never
exposed off-box. Lets a shell drive the UI (button injection) and
inspect engine + UI state without the physical hardware in front of
you.

Endpoints:

    GET  /state          → JSON snapshot of engine + UI + configs.
    POST /press/{btn}    → inject press (UP/DOWN/LEFT/RIGHT/OK/BACK).
    POST /release/{btn}  → inject release.
    POST /tap/{btn}      → press + brief delay + release (short tap).
    POST /hold/{btn}/{ms}→ press, sleep N ms, release (long-press).
    GET  /frame.png      → current rendered 320×240 frame as PNG.

Design notes:

- Button injection pushes into the same `ButtonEventQueue` that
  gpiozero / Tk write to, so the main loop drains them on its next
  tick. Latency = up to one tick (100 ms), invisible for testing.
- State is read directly from `app` / `engine`. CPython's GIL makes
  single-attribute reads atomic, and the fields involved are all
  primitives (int / str / None) — fine without explicit locking for
  this use case. The next event-driven refactor will add an `app.lock`
  that this server should adopt too.
- The frame.png endpoint re-renders on demand. It does NOT cache the
  last rendered frame from the main loop, to avoid coupling.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from .buttons.iface import ButtonId

logger = logging.getLogger(__name__)


DEFAULT_PORT = 9999

_BUTTONS_BY_NAME = {b.name: b for b in ButtonId}


def _btn(name: str) -> Optional[ButtonId]:
    return _BUTTONS_BY_NAME.get(name.upper())


def _app_snapshot(app: Any) -> dict:
    """Build a JSON-safe state snapshot.

    `app` is the live `App` instance; reads here race with the main
    loop but only on primitive fields, so we accept the slight
    inconsistency for the testing use case.

    The `camera.live` block is best-effort: it issues PTP calls and may
    fail (camera busy mid-shoot, disconnected, etc.). On failure we
    just omit those fields rather than 500-ing.
    """
    status = app.engine.status()
    state_name = getattr(app.state, "name", None) or str(app.state)

    camera: dict[str, Any] = {"connected": app.camera.is_connected()}
    if camera["connected"] and status.state != "running":
        # `status()` on the real Sigma adapter issues 3 PTP roundtrips
        # — cheap when the engine is idle, but we skip it while RUNNING
        # so we don't compete with `shoot()` for the USB bus.
        try:
            s = app.camera.status()
            camera["live"] = {
                "focus_mode": s.focus_mode.value if s.focus_mode is not None else None,
                "exposure_mode": (
                    s.exposure_mode.value if s.exposure_mode is not None else None
                ),
                "shutter_s": s.shutter_s,
                "aperture": s.aperture,
                "iso": s.iso,
                "iso_auto": s.iso_auto,
            }
        except Exception as e:
            camera["live_error"] = f"{type(e).__name__}: {e}"

    return {
        "engine": {
            "state": status.state.value if status.state is not None else None,
            "active_config_name": status.active_config_name,
            "shots_taken": status.shots_taken,
            "skips": status.skips,
            "consecutive_failures": status.consecutive_failures,
            "seconds_to_next_shot": status.seconds_to_next_shot,
        },
        "ui": {
            "screen": state_name,
            "main_cursor": app.main_ix.cursor,
        },
        "camera": camera,
        "configs": [
            {
                "name": c.name,
                "interval_s": c.interval_s,
                "shots": [
                    {
                        "shutter": s.shutter,
                        "iso": s.iso,
                        "aperture": s.aperture,
                    }
                    for s in c.shots
                ],
            }
            for c in app.configs
        ],
    }


def _make_handler(
    inject_press: Callable[[ButtonId], None],
    inject_release: Callable[[ButtonId], None],
    snapshot: Callable[[], dict],
    render_frame: Callable[[], Any],  # returns PIL.Image
) -> type:
    """Closure-based handler factory.

    Avoids subclassing with mutable class attrs (which would prevent
    multiple servers / make tests messy).
    """

    class Handler(BaseHTTPRequestHandler):
        # Silence default per-request logging — the global logger is
        # enough and the default spams stderr.
        def log_message(self, format, *args):  # noqa: A002, N802
            logger.debug("control: " + format, *args)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"),
                       "application/json")

        def _send_err(self, code: int, msg: str) -> None:
            self._send_json({"ok": False, "error": msg}, code=code)

        def _parse_btn(self, parts: list[str], idx: int) -> Optional[ButtonId]:
            if len(parts) <= idx:
                self._send_err(400, "missing button name")
                return None
            bid = _btn(parts[idx])
            if bid is None:
                self._send_err(
                    400,
                    f"unknown button {parts[idx]!r}; "
                    f"valid: {sorted(_BUTTONS_BY_NAME)}",
                )
                return None
            return bid

        def do_GET(self) -> None:  # noqa: N802
            parts = [p for p in self.path.split("/") if p]
            if parts == ["state"]:
                self._send_json({"ok": True, **snapshot()})
                return
            if parts == ["frame.png"]:
                img = render_frame()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                self._send(200, buf.getvalue(), "image/png")
                return
            self._send_err(404, f"unknown GET {self.path}")

        def do_POST(self) -> None:  # noqa: N802
            parts = [p for p in self.path.split("/") if p]
            if not parts:
                self._send_err(404, "empty path")
                return
            verb = parts[0]
            if verb == "press":
                bid = self._parse_btn(parts, 1)
                if bid is None:
                    return
                inject_press(bid)
                self._send_json({"ok": True, "pressed": bid.name})
                return
            if verb == "release":
                bid = self._parse_btn(parts, 1)
                if bid is None:
                    return
                inject_release(bid)
                self._send_json({"ok": True, "released": bid.name})
                return
            if verb == "tap":
                bid = self._parse_btn(parts, 1)
                if bid is None:
                    return
                inject_press(bid)
                time.sleep(0.05)
                inject_release(bid)
                self._send_json({"ok": True, "tapped": bid.name})
                return
            if verb == "hold":
                bid = self._parse_btn(parts, 1)
                if bid is None:
                    return
                if len(parts) < 3:
                    self._send_err(400, "missing hold duration (ms)")
                    return
                try:
                    ms = int(parts[2])
                except ValueError:
                    self._send_err(400, f"bad duration {parts[2]!r}")
                    return
                inject_press(bid)
                time.sleep(ms / 1000.0)
                inject_release(bid)
                self._send_json(
                    {"ok": True, "held": bid.name, "duration_ms": ms},
                )
                return
            self._send_err(404, f"unknown POST {self.path}")

    return Handler


class ControlServer:
    """Daemon-thread HTTP server. Stops automatically when the app exits."""

    def __init__(
        self,
        *,
        inject_press: Callable[[ButtonId], None],
        inject_release: Callable[[ButtonId], None],
        snapshot: Callable[[], dict],
        render_frame: Callable[[], Any],
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
    ) -> None:
        handler_cls = _make_handler(
            inject_press, inject_release, snapshot, render_frame,
        )
        self._server = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fp-lapse-control",
            daemon=True,
        )
        self.host = host
        self.port = port

    def start(self) -> None:
        self._thread.start()
        logger.info(
            "control server listening on http://%s:%d (set FP_LAPSE_CONTROL=0 to disable)",
            self.host, self.port,
        )

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
