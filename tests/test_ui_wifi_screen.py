"""Tests for the Wi-Fi list + status screens (wifi-manual-config §4)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.buttons.iface import ButtonId  # noqa: E402
from fp_lapse.display.iface import HEIGHT, WIDTH, new_canvas  # noqa: E402
from fp_lapse.net.nmcli import ConnectOutcome, WifiNetwork  # noqa: E402
from fp_lapse.ui import (  # noqa: E402
    WifiListAction,
    WifiListInteraction,
    WifiListState,
    WifiStatusState,
    render_wifi_list,
    render_wifi_status,
)


NETS = (
    WifiNetwork("MyHomeWiFi", 72, secured=True, active=True, saved=True),
    WifiNetwork("CoffeeShop", 48, secured=False, active=False, saved=False),
    WifiNetwork("Router_2G", 30, secured=True, active=False, saved=False),
)

MOCKUPS_DIR = Path(__file__).resolve().parents[1] / "docs" / "mockups"

# Same fixtures the mockup renderer uses (docs/mockups/render_mockups.py).
_MOCKUP_NETS = (
    WifiNetwork("MyHomeWiFi", 72, secured=True, active=True, saved=True),
    WifiNetwork("Guest_Network", 55, secured=True, active=False, saved=False),
    WifiNetwork("CoffeeShop", 48, secured=False, active=False, saved=False),
    WifiNetwork("Router_5G", 30, secured=True, active=False, saved=False),
    WifiNetwork("Cabin_5G", 12, secured=True, active=False, saved=False),
)


def _short_ok(ix):
    """Press + release OK (a short tap), returning the release action."""
    ix.on_press(ButtonId.OK)
    return ix.on_release(ButtonId.OK)


def _short_back(ix):
    ix.on_press(ButtonId.BACK)
    return ix.on_release(ButtonId.BACK)


class TestListInteraction(unittest.TestCase):
    """Revision 1 — OK/BACK fire on RELEASE; long-press splits edit/forget."""

    def test_navigation_covers_networks_plus_pseudo(self):
        ix = WifiListInteraction(NETS)
        for _ in range(10):
            ix.on_press(ButtonId.DOWN)
        # 3 networks + Other + Rescan = 5 items, last index 4.
        self.assertEqual(ix.cursor, 4)

    def test_ok_press_returns_none_action_on_release(self):
        # The press itself yields no action; the short action arrives on
        # release so short/long can be distinguished.
        ix = WifiListInteraction(NETS)
        self.assertIsNone(ix.on_press(ButtonId.OK))
        self.assertIs(ix.on_release(ButtonId.OK), WifiListAction.CONNECT)

    def test_ok_on_network_returns_connect(self):
        ix = WifiListInteraction(NETS)
        self.assertIs(_short_ok(ix), WifiListAction.CONNECT)

    def test_ok_on_other_returns_other(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = len(NETS)
        self.assertIs(_short_ok(ix), WifiListAction.OTHER)

    def test_ok_on_rescan_returns_rescan(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = len(NETS) + 1
        self.assertIs(_short_ok(ix), WifiListAction.RESCAN)

    def test_back_returns_cancel_on_release(self):
        ix = WifiListInteraction(NETS)
        self.assertIsNone(ix.on_press(ButtonId.BACK))
        self.assertIs(ix.on_release(ButtonId.BACK), WifiListAction.CANCEL)

    def test_hold_back_does_not_also_fire_cancel(self):
        # A held BACK fires FORGET via on_long_press; the trailing
        # release must NOT then also emit CANCEL.
        ix = WifiListInteraction(NETS)
        ix.cursor = 0  # saved
        ix.on_press(ButtonId.BACK)
        self.assertIs(ix.on_long_press(ButtonId.BACK), WifiListAction.FORGET)
        self.assertIsNone(ix.on_release(ButtonId.BACK))

    def test_hold_ok_edit_only_on_secured(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = 0  # MyHomeWiFi (secured)
        ix.on_press(ButtonId.OK)
        self.assertIs(ix.on_long_press(ButtonId.OK), WifiListAction.EDIT)
        # An open network → no edit.
        ix2 = WifiListInteraction(NETS)
        ix2.cursor = 1  # CoffeeShop (open)
        ix2.on_press(ButtonId.OK)
        self.assertIsNone(ix2.on_long_press(ButtonId.OK))

    def test_hold_ok_edit_guarded_by_press(self):
        # on_long_press with no preceding OK press is a stale fire → None.
        ix = WifiListInteraction(NETS)
        ix.cursor = 0
        self.assertIsNone(ix.on_long_press(ButtonId.OK))

    def test_hold_ok_then_release_does_not_fire_short_action(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = 0
        ix.on_press(ButtonId.OK)
        ix.on_long_press(ButtonId.OK)  # EDIT fired
        self.assertIsNone(ix.on_release(ButtonId.OK))

    def test_hold_back_forget_only_on_saved(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = 0  # MyHomeWiFi (saved)
        ix.on_press(ButtonId.BACK)
        self.assertIs(ix.on_long_press(ButtonId.BACK), WifiListAction.FORGET)
        # A non-saved network → no forget.
        ix2 = WifiListInteraction(NETS)
        ix2.cursor = 1  # CoffeeShop (not saved)
        ix2.on_press(ButtonId.BACK)
        self.assertIsNone(ix2.on_long_press(ButtonId.BACK))

    def test_hold_back_forget_guarded_by_press(self):
        # The `_back_pressed` guard: a long-press BACK with no preceding
        # BACK press (e.g. it cancelled a keyboard, never reached the
        # list) must NOT fire FORGET.
        ix = WifiListInteraction(NETS)
        ix.cursor = 0  # saved
        self.assertIsNone(ix.on_long_press(ButtonId.BACK))

    def test_long_press_none_on_pseudo_items(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = len(NETS)       # Other…
        ix.on_press(ButtonId.OK)
        self.assertIsNone(ix.on_long_press(ButtonId.OK))
        ix.cursor = len(NETS) + 1   # Rescan
        ix.on_press(ButtonId.OK)
        self.assertIsNone(ix.on_long_press(ButtonId.OK))

    def test_reset_input_clears_flags(self):
        ix = WifiListInteraction(NETS)
        ix.cursor = 0
        ix.on_press(ButtonId.BACK)   # arms _back_pressed
        ix.reset_input()
        # A stale long-press after reset is a no-op (guard restored).
        self.assertIsNone(ix.on_long_press(ButtonId.BACK))
        # And a release after reset doesn't emit CANCEL.
        self.assertIsNone(ix.on_release(ButtonId.BACK))

    def test_selected_network(self):
        ix = WifiListInteraction(NETS)
        state = WifiListState(NETS, cursor=1, scanning=False)
        ix.cursor = 1
        self.assertEqual(ix.selected_network(state).ssid, "CoffeeShop")
        ix.cursor = len(NETS)  # pseudo item
        self.assertIsNone(ix.selected_network(state))

    def test_empty_list_only_pseudo_items(self):
        ix = WifiListInteraction(())
        self.assertIs(_short_ok(ix), WifiListAction.OTHER)
        ix.on_press(ButtonId.DOWN)
        self.assertIs(_short_ok(ix), WifiListAction.RESCAN)


class TestListRender(unittest.TestCase):
    def test_renders_320x240(self):
        out = render_wifi_list(
            new_canvas(), WifiListState(NETS, 0, scanning=False), dots=None,
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))
        self.assertEqual(out.mode, "RGB")

    def test_empty_does_not_crash(self):
        out = render_wifi_list(
            new_canvas(), WifiListState((), 0, scanning=False), dots=None,
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))

    def test_scanning_header_animates(self):
        base = new_canvas()
        a = render_wifi_list(base, WifiListState(NETS, 0, scanning=True), dots=1).tobytes()
        b = render_wifi_list(base, WifiListState(NETS, 0, scanning=True), dots=3).tobytes()
        self.assertNotEqual(a, b)

    def test_cursor_changes_pixels(self):
        base = new_canvas()
        a = render_wifi_list(base, WifiListState(NETS, 0, scanning=False), dots=None).tobytes()
        b = render_wifi_list(base, WifiListState(NETS, 1, scanning=False), dots=None).tobytes()
        self.assertNotEqual(a, b)


class TestStatusRender(unittest.TestCase):
    def test_connecting(self):
        out = render_wifi_status(
            new_canvas(), WifiStatusState(phase="connecting", ssid="MyHomeWiFi"),
            dots=2,
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))

    def test_connecting_dots_animate(self):
        base = new_canvas()
        a = render_wifi_status(base, WifiStatusState("connecting", "AP"), dots=1).tobytes()
        b = render_wifi_status(base, WifiStatusState("connecting", "AP"), dots=3).tobytes()
        self.assertNotEqual(a, b)

    def test_connected_with_ip(self):
        out = render_wifi_status(
            new_canvas(),
            WifiStatusState(phase="connected", ssid="MyHomeWiFi", ip="192.168.1.42"),
            dots=None,
        )
        self.assertEqual(out.size, (WIDTH, HEIGHT))

    def test_failed_each_outcome_renders(self):
        base = new_canvas()
        seen = set()
        for oc in (ConnectOutcome.BAD_AUTH, ConnectOutcome.NOT_FOUND,
                   ConnectOutcome.TIMEOUT, ConnectOutcome.FAILED):
            img = render_wifi_status(
                base,
                WifiStatusState(phase="failed", ssid="AP", outcome=oc, detail="x"),
                dots=None,
            ).tobytes()
            seen.add(img)
        # BAD_AUTH/NOT_FOUND/TIMEOUT carry distinct messages; FAILED may
        # match one — at least 3 distinct renders expected.
        self.assertGreaterEqual(len(seen), 3)


class TestWifiVisualRegression(unittest.TestCase):
    """Pixel-exact match against docs/mockups/*.png (base discarded by the
    overlay canvas, so a blank base reproduces the mockup bytes)."""

    def _assert(self, actual: Image.Image, name: str) -> None:
        path = MOCKUPS_DIR / f"{name}.png"
        self.assertTrue(path.exists(), f"missing mockup: {path}")
        expected = Image.open(path).convert("RGB")
        self.assertEqual(actual.tobytes(), expected.tobytes(),
                         f"{name}.png differs from production render")

    def test_21_wifi_list(self):
        self._assert(
            render_wifi_list(
                new_canvas(), WifiListState(_MOCKUP_NETS, 0, scanning=False),
                dots=None,
            ),
            "21_wifi_list",
        )

    def test_24_wifi_connecting(self):
        self._assert(
            render_wifi_status(
                new_canvas(), WifiStatusState(phase="connecting", ssid="MyHomeWiFi"),
                dots=2,
            ),
            "24_wifi_connecting",
        )

    def test_25_wifi_connected(self):
        self._assert(
            render_wifi_status(
                new_canvas(),
                WifiStatusState(phase="connected", ssid="MyHomeWiFi", ip="192.168.1.42"),
                dots=None,
            ),
            "25_wifi_connected",
        )

    def test_26_wifi_failed(self):
        self._assert(
            render_wifi_status(
                new_canvas(),
                WifiStatusState(
                    phase="failed", ssid="MyHomeWiFi",
                    outcome=ConnectOutcome.BAD_AUTH, detail="secrets were required",
                ),
                dots=None,
            ),
            "26_wifi_failed",
        )

    def test_27_wifi_forget_confirm(self):
        from fp_lapse.ui import render_overlay, wifi_forget_confirm
        base = render_wifi_list(
            new_canvas(), WifiListState(_MOCKUP_NETS, 0, scanning=False), dots=None,
        )
        self._assert(
            render_overlay(base, wifi_forget_confirm("MyHomeWiFi")),
            "27_wifi_forget_confirm",
        )


if __name__ == "__main__":
    unittest.main()
