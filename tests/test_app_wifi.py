"""App-level Wi-Fi flow tests (wifi-manual-config §5).

Drives `App` with a fake nmcli facade + a synchronous worker spawner so
every flow in the Acceptance Criteria runs deterministically — no real
subprocess, no real threads, no `time.sleep`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, HERE)  # so `import test_app` works in both run modes

from fp_lapse.app import App, AppState  # noqa: E402
from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.camera import MockCamera  # noqa: E402
from fp_lapse.configs import ConfigStore, Shot, TimelapseConfig  # noqa: E402
from fp_lapse.engine import Engine  # noqa: E402
from fp_lapse.net.nmcli import (  # noqa: E402
    ConnectOutcome,
    ConnectResult,
    WifiNetwork,
    WifiStatus,
)
from fp_lapse.ui import KeyboardInteraction  # noqa: E402
from fp_lapse.ui.keyboard import KeyKind, keyboard_rows  # noqa: E402


# Reuse the synchronous scheduler stand-in from test_app.
from test_app import _SyncScheduler  # noqa: E402


SECURED = WifiNetwork("MyHomeWiFi", 72, secured=True, active=True, saved=True)
OPEN = WifiNetwork("CoffeeShop", 48, secured=False, active=False, saved=False)
SECURED_UNSAVED = WifiNetwork("Router_2G", 30, secured=True, active=False, saved=False)


class _FakeNmcli:
    def __init__(self, *, networks=(), connect=None, status_ip="192.168.1.42"):
        self._networks = list(networks)
        self._connect_result = connect
        self._status_ip = status_ip
        self.calls = []
        self.forgotten = []
        # Models the live association: seeded from the initially-active
        # network, updated on a successful connect. `scan`/`status` report
        # against it, like real nmcli (so post-connect refresh is testable).
        self._active = next((n.ssid for n in self._networks if n.active), None)

    def scan(self, *, rescan=False):
        self.calls.append(("scan", rescan))
        return [
            replace(n, active=(n.ssid == self._active))
            for n in self._networks if n.ssid not in self.forgotten
        ]

    def connect(self, ssid, password=None, *, hidden=False):
        self.calls.append(("connect", ssid, password, hidden))
        res = (
            self._connect_result if self._connect_result is not None
            else ConnectResult(ConnectOutcome.SUCCESS, ssid)
        )
        if res.outcome is ConnectOutcome.SUCCESS:
            self._active = ssid
        return res

    def status(self):
        return WifiStatus(self._active or "MyHomeWiFi", self._status_ip)

    def forget(self, ssid):
        self.calls.append(("forget", ssid))
        self.forgotten.append(ssid)
        return True


def _sync_spawner(fn):
    fn()


def _make_app(test, *, networks=(), connect=None, spawner=_sync_spawner):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    store = ConfigStore(Path(tmp.name) / "configs.json")
    store.save([TimelapseConfig("A", 10.0, (Shot(shutter=1 / 500, iso=200),))])
    camera = MockCamera(sleep_overhead_s=0.0)
    camera.connect()
    engine = Engine(camera)
    fake = _FakeNmcli(networks=networks, connect=connect)
    app = App(
        scheduler=_SyncScheduler(engine), store=store, camera=camera,
        nmcli=fake, wifi_worker_spawner=spawner,
    )
    return app, fake


def _open_wifi_list(app):
    """Main → LEFT (SETTINGS) → cursor to Wi-Fi setup → OK."""
    app.on_press(ButtonId.LEFT)
    assert app.state == AppState.TIME_SETUP
    app.on_press(ButtonId.DOWN)
    app.on_press(ButtonId.DOWN)   # cursor on "Wi-Fi setup" (index 2)
    app.on_press(ButtonId.OK)


# Revision 1 — list OK/BACK fire on RELEASE; long-press = edit/forget.
def _short_ok(app):
    app.on_press(ButtonId.OK)
    app.on_release(ButtonId.OK)


def _short_back(app):
    app.on_press(ButtonId.BACK)
    app.on_release(ButtonId.BACK)


def _hold_ok(app):
    """Press OK then fire the long-press timer (router cancels release)."""
    app.on_press(ButtonId.OK)
    app.on_long_press(ButtonId.OK)


def _hold_back(app):
    app.on_press(ButtonId.BACK)
    app.on_long_press(ButtonId.BACK)


def _type_and_done(app, password):
    """Replace the live keyboard with one pre-filled, then press DONE."""
    target = app.keyboard_ix.target
    app.keyboard_ix = KeyboardInteraction(target=target, initial=password)
    kb = app.keyboard_ix
    rows = keyboard_rows(kb.target, kb.state.layer)
    done_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.DONE)
    while kb.state.cursor_row < 3:
        app.on_press(ButtonId.DOWN)
    for _ in range(12):
        if kb.state.cursor_col == 0:
            break
        app.on_press(ButtonId.LEFT)
    for _ in range(done_col):
        app.on_press(ButtonId.RIGHT)
    app.on_press(ButtonId.OK)


class TestEnterFlow(unittest.TestCase):
    def test_wifi_setup_enters_list_with_cached_scan(self):
        app, fake = _make_app(self, networks=(SECURED, OPEN))
        _open_wifi_list(app)
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertEqual(len(app.wifi_networks), 2)
        self.assertIn(("scan", False), fake.calls)  # cached scan, no rescan


class TestSecuredConnect(unittest.TestCase):
    def test_unsaved_secured_opens_password_keyboard_then_connects(self):
        # Revision 1: a secured network with NO saved profile opens the
        # keyboard on a short OK.
        app, fake = _make_app(self, networks=(SECURED_UNSAVED,))
        _open_wifi_list(app)
        _short_ok(app)  # short OK on the unsaved secured network
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        self.assertEqual(app.keyboard_ix.target, "password")
        _type_and_done(app, "secret12")
        # Synchronous worker → already settled.
        self.assertEqual(app.state, AppState.WIFI_STATUS)
        self.assertEqual(app.wifi_status_state.phase, "connected")
        self.assertEqual(app.wifi_status_state.ip, "192.168.1.42")
        connect_call = next(c for c in fake.calls if c[0] == "connect")
        self.assertEqual(connect_call[1], "Router_2G")
        self.assertEqual(connect_call[2], "secret12")
        self.assertFalse(connect_call[3])  # not hidden

    def test_saved_secured_short_ok_connects_with_stored_creds(self):
        # Revision 1: a SAVED secured network connects immediately with
        # stored secrets — no keyboard, connect(password=None).
        app, fake = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _short_ok(app)  # short OK on the saved secured network
        self.assertEqual(app.state, AppState.WIFI_STATUS)
        self.assertEqual(app.wifi_status_state.phase, "connected")
        connect_call = next(c for c in fake.calls if c[0] == "connect")
        self.assertEqual(connect_call[1], "MyHomeWiFi")
        self.assertIsNone(connect_call[2])  # stored creds reused
        self.assertFalse(connect_call[3])

    def test_hold_ok_edits_password_on_secured_then_connects(self):
        # Revision 1: hold OK on a secured network (saved here) opens the
        # password keyboard; Done connects with the new password.
        app, fake = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _hold_ok(app)  # edit password
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        self.assertEqual(app.keyboard_ix.target, "password")
        _type_and_done(app, "newpass1")
        connect_call = next(c for c in fake.calls if c[0] == "connect")
        self.assertEqual(connect_call[1], "MyHomeWiFi")
        self.assertEqual(connect_call[2], "newpass1")  # freshly typed

    def test_hold_ok_on_open_network_is_noop(self):
        app, fake = _make_app(self, networks=(OPEN,))
        _open_wifi_list(app)
        _hold_ok(app)
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertFalse(any(c[0] == "connect" for c in fake.calls))


class TestOpenConnect(unittest.TestCase):
    def test_open_network_skips_keyboard(self):
        app, fake = _make_app(self, networks=(OPEN,))
        _open_wifi_list(app)
        _short_ok(app)
        self.assertEqual(app.state, AppState.WIFI_STATUS)
        self.assertEqual(app.wifi_status_state.phase, "connected")
        connect_call = next(c for c in fake.calls if c[0] == "connect")
        self.assertEqual(connect_call[1], "CoffeeShop")
        self.assertIsNone(connect_call[2])  # no password


class TestActiveDotRefresh(unittest.TestCase):
    """A successful connect must move the active `●` marker (and `saved`)
    onto the newly-joined network, not keep the stale entry-scan flag."""

    def test_connect_moves_active_flag_to_new_network(self):
        app, _fake = _make_app(
            self, networks=(SECURED, OPEN, SECURED_UNSAVED),
        )
        _open_wifi_list(app)
        # MyHomeWiFi is active on entry.
        nets = {n.ssid: n for n in app.wifi_networks}
        self.assertTrue(nets["MyHomeWiFi"].active)
        self.assertFalse(nets["CoffeeShop"].active)
        # Connect to the open network (cursor 1).
        app.on_press(ButtonId.DOWN)
        _short_ok(app)
        self.assertEqual(app.wifi_status_state.phase, "connected")
        # The list is refreshed: the dot followed the association.
        nets = {n.ssid: n for n in app.wifi_networks}
        self.assertTrue(nets["CoffeeShop"].active)
        self.assertFalse(nets["MyHomeWiFi"].active)
        # CoffeeShop now has a profile → saved.
        self.assertTrue(nets["CoffeeShop"].saved)

    def test_failed_connect_does_not_refresh_active(self):
        app, _fake = _make_app(
            self, networks=(SECURED, SECURED_UNSAVED),
            connect=ConnectResult(ConnectOutcome.BAD_AUTH, "Router_2G"),
        )
        _open_wifi_list(app)
        app.on_press(ButtonId.DOWN)  # Router_2G (unsaved secured)
        _short_ok(app)               # opens password keyboard
        _type_and_done(app, "wrongpass")
        self.assertEqual(app.wifi_status_state.phase, "failed")
        # No spurious active move: MyHomeWiFi stays the active one.
        nets = {n.ssid: n for n in app.wifi_networks}
        self.assertTrue(nets["MyHomeWiFi"].active)
        self.assertFalse(nets["Router_2G"].active)


class TestBadPassword(unittest.TestCase):
    def test_bad_auth_failure_then_retry(self):
        app, _ = _make_app(
            self, networks=(SECURED_UNSAVED,),
            connect=ConnectResult(ConnectOutcome.BAD_AUTH, "Router_2G", "secrets"),
        )
        _open_wifi_list(app)
        _short_ok(app)
        _type_and_done(app, "wrongpwd")
        self.assertEqual(app.wifi_status_state.phase, "failed")
        self.assertIs(app.wifi_status_state.outcome, ConnectOutcome.BAD_AUTH)
        # OK retries → back to the list.
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.WIFI_LIST)

    def test_failed_connect_does_not_disturb_engine(self):
        app, _ = _make_app(
            self, networks=(SECURED_UNSAVED,),
            connect=ConnectResult(ConnectOutcome.BAD_AUTH, "Router_2G"),
        )
        from fp_lapse.engine import EngineState
        _open_wifi_list(app)
        _short_ok(app)
        _type_and_done(app, "wrongpwd")
        self.assertEqual(app.engine.state, EngineState.IDLE)


class TestTimeout(unittest.TestCase):
    def test_timeout_outcome(self):
        app, _ = _make_app(
            self, networks=(SECURED_UNSAVED,),
            connect=ConnectResult(ConnectOutcome.TIMEOUT, "Router_2G", "30 s"),
        )
        _open_wifi_list(app)
        _short_ok(app)
        _type_and_done(app, "secret12")
        self.assertEqual(app.wifi_status_state.phase, "failed")
        self.assertIs(app.wifi_status_state.outcome, ConnectOutcome.TIMEOUT)


class TestHiddenNetwork(unittest.TestCase):
    def test_other_network_ssid_then_password_then_hidden_connect(self):
        app, fake = _make_app(self, networks=())
        _open_wifi_list(app)
        # cursor 0 == Other network… (empty list)
        _short_ok(app)
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        self.assertEqual(app.keyboard_ix.target, "ssid")
        _type_and_done(app, "HiddenNet")
        # Now a password keyboard.
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        self.assertEqual(app.keyboard_ix.target, "password")
        _type_and_done(app, "secret12")
        connect_call = next(c for c in fake.calls if c[0] == "connect")
        self.assertEqual(connect_call[1], "HiddenNet")
        self.assertTrue(connect_call[3])  # hidden=True


class TestEmptyScanAndRescan(unittest.TestCase):
    def test_empty_scan_shows_pseudo_items(self):
        app, _ = _make_app(self, networks=())
        _open_wifi_list(app)
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertEqual(app.wifi_networks, ())

    def test_rescan_repopulates(self):
        app, fake = _make_app(self, networks=(SECURED,))
        # Enter with an empty cached list by clearing fake networks first.
        fake._networks = []
        _open_wifi_list(app)
        self.assertEqual(app.wifi_networks, ())
        # Now add a network and Rescan (cursor on Rescan = index 1 of 2 pseudo).
        fake._networks = [SECURED]
        app.on_press(ButtonId.DOWN)   # cursor → Rescan
        _short_ok(app)
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertEqual(len(app.wifi_networks), 1)
        self.assertTrue(any(c[0] == "scan" and c[1] is True for c in fake.calls))


class TestForget(unittest.TestCase):
    def test_hold_back_saved_opens_forget_overlay_and_deletes(self):
        # Revision 1: forget is hold BACK on a saved network.
        app, fake = _make_app(self, networks=(SECURED, SECURED_UNSAVED))
        _open_wifi_list(app)
        _hold_back(app)                  # SECURED (cursor 0) is saved
        self.assertEqual(app.state, AppState.OVERLAY_WIFI_FORGET)
        self.assertEqual(app._wifi_forget_target, "MyHomeWiFi")
        app.on_press(ButtonId.OK)        # confirm
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertIn(("forget", "MyHomeWiFi"), fake.calls)
        self.assertNotIn("MyHomeWiFi", [n.ssid for n in app.wifi_networks])

    def test_forget_cancel_keeps_network(self):
        app, fake = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _hold_back(app)
        app.on_press(ButtonId.BACK)      # cancel the overlay
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertNotIn(("forget", "MyHomeWiFi"), fake.calls)
        self.assertIn("MyHomeWiFi", [n.ssid for n in app.wifi_networks])

    def test_hold_back_unsaved_does_nothing(self):
        app, _ = _make_app(self, networks=(SECURED_UNSAVED,))
        _open_wifi_list(app)
        _hold_back(app)
        self.assertEqual(app.state, AppState.WIFI_LIST)

    def test_hold_ok_does_not_forget(self):
        # hold OK on a saved secured network is EDIT, never FORGET.
        app, fake = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _hold_ok(app)
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        self.assertFalse(any(c[0] == "forget" for c in fake.calls))


class TestCancelRestores(unittest.TestCase):
    def test_short_back_from_list_returns_to_settings(self):
        app, _ = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _short_back(app)
        self.assertEqual(app.state, AppState.TIME_SETUP)
        self.assertEqual(app.time_setup_ix.cursor, 2)

    def test_back_from_keyboard_returns_to_list(self):
        app, _ = _make_app(self, networks=(SECURED_UNSAVED,))
        _open_wifi_list(app)
        _short_ok(app)                   # unsaved secured → password keyboard
        app.on_press(ButtonId.BACK)      # cancel keyboard
        self.assertEqual(app.state, AppState.WIFI_LIST)

    def test_held_back_cancelling_keyboard_does_not_forget(self):
        # Revision 1 `_back_pressed` guard: a held BACK that cancels the
        # keyboard returns to the list; a trailing long-press BACK fire
        # must NOT forget (reset_input cleared the flag on return).
        # Use a SAVED network so that, WITHOUT the guard, the long-press
        # BACK would otherwise be eligible to FORGET.
        app, fake = _make_app(self, networks=(SECURED,))
        _open_wifi_list(app)
        _hold_ok(app)                    # EDIT → password keyboard (saved net)
        self.assertEqual(app.state, AppState.WIFI_KEYBOARD)
        app.on_press(ButtonId.BACK)      # cancels keyboard → back to list
        self.assertEqual(app.state, AppState.WIFI_LIST)
        # The router's BACK long-press timer fires while still held.
        app.on_long_press(ButtonId.BACK)
        self.assertEqual(app.state, AppState.WIFI_LIST)
        self.assertFalse(any(c[0] == "forget" for c in fake.calls))


class TestMaskingRoundTrip(unittest.TestCase):
    def test_mask_toggle_keeps_text(self):
        app, _ = _make_app(self, networks=(SECURED_UNSAVED,))
        _open_wifi_list(app)
        _short_ok(app)                   # unsaved secured → password keyboard
        app.keyboard_ix = KeyboardInteraction(target="password", initial="secret12")
        # Toggle mask via the MASK key.
        kb = app.keyboard_ix
        rows = keyboard_rows(kb.target, kb.state.layer)
        mask_col = next(i for i, k in enumerate(rows[-1]) if k.kind == KeyKind.MASK)
        while kb.state.cursor_row < 3:
            app.on_press(ButtonId.DOWN)
        for _ in range(12):
            if kb.state.cursor_col == 0:
                break
            app.on_press(ButtonId.LEFT)
        for _ in range(mask_col):
            app.on_press(ButtonId.RIGHT)
        before = kb.text
        app.on_press(ButtonId.OK)
        self.assertFalse(kb.state.masked)
        self.assertEqual(kb.text, before)


class TestBusyGate(unittest.TestCase):
    def test_buttons_inert_while_connecting(self):
        # A spawner that does NOT run the worker leaves _wifi_busy True.
        def no_run(_fn):
            return None
        app, _ = _make_app(self, networks=(OPEN,), spawner=no_run)
        _open_wifi_list(app)
        _short_ok(app)  # open network → connect worker (never runs)
        self.assertTrue(app._wifi_busy)
        self.assertEqual(app.state, AppState.WIFI_STATUS)
        self.assertEqual(app.wifi_status_state.phase, "connecting")
        # Buttons inert: OK does nothing (still connecting).
        app.on_press(ButtonId.OK)
        self.assertEqual(app.state, AppState.WIFI_STATUS)
        self.assertEqual(app.wifi_status_state.phase, "connecting")

    def test_list_buttons_inert_while_scanning(self):
        def no_run(_fn):
            return None
        app, _ = _make_app(self, networks=(SECURED,), spawner=no_run)
        _open_wifi_list(app)
        app.on_press(ButtonId.DOWN)  # move cursor off network
        app.on_press(ButtonId.DOWN)  # cursor on Rescan
        _short_ok(app)               # Rescan worker (never runs) → busy
        self.assertTrue(app._wifi_busy)
        cursor_before = app.wifi_list_ix.cursor
        app.on_press(ButtonId.UP)    # inert
        self.assertEqual(app.wifi_list_ix.cursor, cursor_before)


if __name__ == "__main__":
    unittest.main()
