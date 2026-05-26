"""Smoke + protocol tests for the HTTP control surface.

The server runs against fakes (no GPIO, no display, no camera). We
boot it on an ephemeral port, hit each endpoint with `urllib`, and
verify the wire contract: button injections forward to the right
callbacks, `/state` is JSON, `/frame.png` is bytes of a PNG.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import unittest
import urllib.request
from typing import List, Tuple

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.control_server import ControlServer  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestControlServer(unittest.TestCase):
    def setUp(self) -> None:
        self.events: List[Tuple[ButtonId, str]] = []
        self.snapshot_data = {
            "engine": {"state": "idle", "shots_taken": 0, "skips": 0},
            "ui": {"screen": "MAIN"},
            "camera": {"connected": True},
            "configs": [],
        }
        self.port = _free_port()
        self.server = ControlServer(
            inject_press=lambda b: self.events.append((b, "press")),
            inject_release=lambda b: self.events.append((b, "release")),
            snapshot=lambda: self.snapshot_data,
            render_frame=lambda: Image.new("RGB", (320, 240), (0, 0, 0)),
            port=self.port,
        )
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()

    # --- /state ---

    def test_state_returns_json(self):
        with urllib.request.urlopen(f"{self.base}/state") as r:
            self.assertEqual(r.status, 200)
            payload = json.loads(r.read())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["engine"]["state"], "idle")
        self.assertEqual(payload["ui"]["screen"], "MAIN")

    # --- /press, /release ---

    def test_press_button_injects(self):
        req = urllib.request.Request(
            f"{self.base}/press/OK", method="POST",
        )
        with urllib.request.urlopen(req) as r:
            payload = json.loads(r.read())
        self.assertEqual(payload["pressed"], "OK")
        self.assertEqual(self.events, [(ButtonId.OK, "press")])

    def test_release_button_injects(self):
        req = urllib.request.Request(
            f"{self.base}/release/BACK", method="POST",
        )
        with urllib.request.urlopen(req):
            pass
        self.assertEqual(self.events, [(ButtonId.BACK, "release")])

    def test_unknown_button_returns_400(self):
        req = urllib.request.Request(
            f"{self.base}/press/XYZ", method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_button_name_is_case_insensitive(self):
        req = urllib.request.Request(
            f"{self.base}/press/ok", method="POST",
        )
        with urllib.request.urlopen(req):
            pass
        self.assertEqual(self.events, [(ButtonId.OK, "press")])

    # --- /tap ---

    def test_tap_emits_press_then_release(self):
        req = urllib.request.Request(
            f"{self.base}/tap/DOWN", method="POST",
        )
        with urllib.request.urlopen(req):
            pass
        self.assertEqual(
            self.events,
            [(ButtonId.DOWN, "press"), (ButtonId.DOWN, "release")],
        )

    # --- /hold ---

    def test_hold_emits_press_sleep_release(self):
        req = urllib.request.Request(
            f"{self.base}/hold/OK/150", method="POST",
        )
        import time
        t0 = time.monotonic()
        with urllib.request.urlopen(req) as r:
            payload = json.loads(r.read())
        dt = time.monotonic() - t0
        self.assertEqual(payload["held"], "OK")
        self.assertEqual(payload["duration_ms"], 150)
        # The hold must actually sleep in between — endpoint blocks for
        # ~duration. We allow generous slack for CI variance.
        self.assertGreaterEqual(dt, 0.140)
        self.assertEqual(
            self.events,
            [(ButtonId.OK, "press"), (ButtonId.OK, "release")],
        )

    def test_hold_with_bad_duration_returns_400(self):
        req = urllib.request.Request(
            f"{self.base}/hold/OK/abc", method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    # --- /frame.png ---

    def test_frame_returns_png_bytes(self):
        with urllib.request.urlopen(f"{self.base}/frame.png") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get("Content-Type"), "image/png")
            data = r.read()
        # PNG magic: 89 50 4E 47
        self.assertEqual(data[:4], b"\x89PNG")

    # --- unknown ---

    def test_unknown_endpoint_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"{self.base}/nope")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
