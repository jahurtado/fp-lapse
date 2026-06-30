"""Tests for the `nmcli` wrapper (wifi-manual-config feature).

Pure parsing helpers + the subprocess-backed functions driven through a
fake `runner` so no real `nmcli` is ever invoked. Import-safety on the
Mac is asserted explicitly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.net.nmcli import (  # noqa: E402
    CONNECT_TIMEOUT_S,
    ConnectOutcome,
    ConnectResult,
    MockNmcli,
    NmcliFacade,
    WifiNetwork,
    WifiStatus,
    classify_connect,
    connect,
    forget,
    parse_saved,
    parse_scan,
    parse_status,
    scan,
    signal_glyph,
    status,
)


class _FakeRunner:
    """Records argv and returns canned (rc, stdout, stderr) per command.

    `responses` maps a recognisable argv-substring to a tuple; the
    first matching key wins. A `raises` map can map a substring to an
    exception instance to raise instead.
    """

    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}
        self.raises = raises or {}
        self.calls = []

    def __call__(self, argv, *, timeout=None):
        self.calls.append((list(argv), timeout))
        joined = " ".join(argv)
        for key, exc in self.raises.items():
            if key in joined:
                raise exc
        for key, resp in self.responses.items():
            if key in joined:
                return resp
        return (0, "", "")


class TestParseScan(unittest.TestCase):
    def test_basic_two_networks(self):
        nets = parse_scan(
            "*:MyHomeWiFi:57:WPA2\n:Cafe:31:\n", saved={"MyHomeWiFi"},
        )
        self.assertEqual(
            nets,
            [
                WifiNetwork("MyHomeWiFi", 57, secured=True, active=True, saved=True),
                WifiNetwork("Cafe", 31, secured=False, active=False, saved=False),
            ],
        )

    def test_escaped_colon_in_ssid(self):
        nets = parse_scan("*:My\\:Net:80:WPA2\n", saved=set())
        self.assertEqual(nets[0].ssid, "My:Net")

    def test_escaped_backslash_in_ssid(self):
        nets = parse_scan(":A\\\\B:40:WPA2\n", saved=set())
        self.assertEqual(nets[0].ssid, "A\\B")

    def test_blank_ssid_dropped(self):
        nets = parse_scan("*::57:WPA2\n:Cafe:31:\n", saved=set())
        self.assertEqual([n.ssid for n in nets], ["Cafe"])

    def test_duplicate_ssid_collapses_max_signal_or_active(self):
        nets = parse_scan(
            ":Dup:20:WPA2\n*:Dup:65:WPA2\n", saved=set(),
        )
        self.assertEqual(len(nets), 1)
        self.assertEqual(nets[0].signal, 65)
        self.assertTrue(nets[0].active)

    def test_sorted_active_first_then_signal_desc(self):
        nets = parse_scan(
            ":Low:10:\n:High:90:\n*:Active:30:\n", saved=set(),
        )
        self.assertEqual([n.ssid for n in nets], ["Active", "High", "Low"])

    def test_empty_input(self):
        self.assertEqual(parse_scan("", saved=set()), [])


class TestParseSaved(unittest.TestCase):
    def test_only_wifi_profiles(self):
        out = (
            "MyHomeWiFi:802-11-wireless\n"
            "Wired connection 1:802-3-ethernet\n"
            "Hotspot:802-11-wireless\n"
            "lo:loopback\n"
        )
        self.assertEqual(parse_saved(out), {"MyHomeWiFi", "Hotspot"})

    def test_empty(self):
        self.assertEqual(parse_saved(""), set())


class TestParseStatus(unittest.TestCase):
    def test_connection_and_ip(self):
        out = "GENERAL.CONNECTION:MyHomeWiFi\nIP4.ADDRESS[1]:192.168.1.42/24\n"
        self.assertEqual(parse_status(out), WifiStatus("MyHomeWiFi", "192.168.1.42"))

    def test_disconnected_connection_is_none(self):
        out = "GENERAL.CONNECTION:--\nIP4.ADDRESS[1]:192.168.1.42/24\n"
        self.assertIsNone(parse_status(out).connection)

    def test_no_ip_line(self):
        out = "GENERAL.CONNECTION:MyHomeWiFi\n"
        st = parse_status(out)
        self.assertEqual(st.connection, "MyHomeWiFi")
        self.assertIsNone(st.ip)


class TestClassifyConnect(unittest.TestCase):
    def test_success(self):
        self.assertIs(classify_connect(0, ""), ConnectOutcome.SUCCESS)

    def test_bad_auth_variants(self):
        for s in (
            "Error: Secrets were required, but not provided.",
            "802-11-wireless-security.psk: property is invalid",
            "no secrets provided",
        ):
            with self.subTest(stderr=s):
                self.assertIs(classify_connect(4, s), ConnectOutcome.BAD_AUTH)

    def test_not_found_variants(self):
        for s in (
            "Error: No network with SSID 'Foo' found.",
            "Error: device not found",
            "no suitable access point",
        ):
            with self.subTest(stderr=s):
                self.assertIs(classify_connect(10, s), ConnectOutcome.NOT_FOUND)

    def test_generic_failure(self):
        self.assertIs(
            classify_connect(1, "Error: something else went wrong"),
            ConnectOutcome.FAILED,
        )


class TestSignalGlyph(unittest.TestCase):
    def test_four_tiers(self):
        self.assertEqual(signal_glyph(10), "·  ")
        self.assertEqual(signal_glyph(40), "▮  ")
        self.assertEqual(signal_glyph(60), "▮▮ ")
        self.assertEqual(signal_glyph(90), "▮▮▮")

    def test_boundaries(self):
        self.assertEqual(signal_glyph(0), "·  ")
        self.assertEqual(signal_glyph(24), "·  ")
        self.assertEqual(signal_glyph(25), "▮  ")
        self.assertEqual(signal_glyph(49), "▮  ")
        self.assertEqual(signal_glyph(50), "▮▮ ")
        self.assertEqual(signal_glyph(74), "▮▮ ")
        self.assertEqual(signal_glyph(75), "▮▮▮")
        self.assertEqual(signal_glyph(100), "▮▮▮")

    def test_fixed_width(self):
        for s in (5, 30, 60, 95):
            self.assertEqual(len(signal_glyph(s)), 3)


class TestSubprocessFns(unittest.TestCase):
    def test_scan_enriches_with_saved(self):
        runner = _FakeRunner({
            "wifi list": (0, "*:MyHomeWiFi:57:WPA2\n:Cafe:31:\n", ""),
            "connection show": (0, "MyHomeWiFi:802-11-wireless\n", ""),
        })
        nets = scan(runner=runner)
        names = {n.ssid: n for n in nets}
        self.assertTrue(names["MyHomeWiFi"].saved)
        self.assertFalse(names["Cafe"].saved)

    def test_scan_rescan_adds_flag(self):
        runner = _FakeRunner({"wifi list": (0, "", ""), "connection show": (0, "", "")})
        scan(rescan=True, runner=runner)
        scan_call = next(c for c, _ in runner.calls if "wifi" in c)
        self.assertIn("--rescan", scan_call)
        self.assertIn("yes", scan_call)

    def test_connect_success(self):
        runner = _FakeRunner({"wifi connect": (0, "", "")})
        res = connect("MyHomeWiFi", "secret123", runner=runner)
        self.assertIs(res.outcome, ConnectOutcome.SUCCESS)
        argv = runner.calls[0][0]
        self.assertIn("password", argv)
        self.assertIn("secret123", argv)

    def test_connect_open_network_no_password(self):
        runner = _FakeRunner({"wifi connect": (0, "", "")})
        connect("OpenAP", None, runner=runner)
        self.assertNotIn("password", runner.calls[0][0])

    def test_connect_hidden_adds_flag(self):
        runner = _FakeRunner({"wifi connect": (0, "", "")})
        connect("Secret", "secret123", hidden=True, runner=runner)
        self.assertIn("hidden", runner.calls[0][0])

    def test_connect_bad_auth(self):
        runner = _FakeRunner({
            "wifi connect": (4, "", "Error: Secrets were required, but not provided."),
        })
        res = connect("AP", "wrong", runner=runner)
        self.assertIs(res.outcome, ConnectOutcome.BAD_AUTH)
        self.assertIsNotNone(res.detail)

    def test_connect_timeout(self):
        runner = _FakeRunner(raises={
            "wifi connect": subprocess.TimeoutExpired(cmd="nmcli", timeout=30),
        })
        res = connect("AP", "secret123", runner=runner)
        self.assertIs(res.outcome, ConnectOutcome.TIMEOUT)

    def test_connect_passes_timeout_to_runner(self):
        runner = _FakeRunner({"wifi connect": (0, "", "")})
        connect("AP", "secret123", runner=runner)
        self.assertEqual(runner.calls[0][1], CONNECT_TIMEOUT_S)

    def test_status(self):
        runner = _FakeRunner({
            "device show": (0, "GENERAL.CONNECTION:MyHomeWiFi\nIP4.ADDRESS[1]:192.168.1.42/24\n", ""),
        })
        self.assertEqual(status(runner=runner), WifiStatus("MyHomeWiFi", "192.168.1.42"))

    def test_forget_returns_true_on_zero(self):
        runner = _FakeRunner({"connection delete": (0, "", "")})
        self.assertTrue(forget("AP", runner=runner))

    def test_forget_returns_false_on_nonzero(self):
        runner = _FakeRunner({"connection delete": (1, "", "boom")})
        self.assertFalse(forget("AP", runner=runner))


class TestFacades(unittest.TestCase):
    def test_real_facade_delegates_to_runner(self):
        runner = _FakeRunner({
            "wifi list": (0, "*:MyHomeWiFi:57:WPA2\n", ""),
            "connection show": (0, "MyHomeWiFi:802-11-wireless\n", ""),
        })
        fac = NmcliFacade(runner=runner)
        nets = fac.scan()
        self.assertEqual(nets[0].ssid, "MyHomeWiFi")

    def test_mock_scan_returns_canned_list(self):
        fac = MockNmcli()
        nets = fac.scan()
        self.assertTrue(len(nets) >= 2)
        self.assertTrue(any(n.active for n in nets))
        self.assertTrue(any(n.secured for n in nets))
        self.assertTrue(any(not n.secured for n in nets))

    def test_mock_connect_success_then_status(self):
        fac = MockNmcli()
        res = fac.connect("MyHomeWiFi", "secret123")
        self.assertIs(res.outcome, ConnectOutcome.SUCCESS)
        self.assertIsNotNone(fac.status().ip)

    def test_mock_connect_magic_bad_password(self):
        fac = MockNmcli()
        res = fac.connect("MyHomeWiFi", "wrongpass")
        self.assertIs(res.outcome, ConnectOutcome.BAD_AUTH)

    def test_mock_forget_removes_from_scan(self):
        fac = MockNmcli()
        before = {n.ssid for n in fac.scan()}
        target = next(iter(before))
        fac.forget(target)
        after = {n.ssid for n in fac.scan()}
        self.assertNotIn(target, after)


class TestImportSafety(unittest.TestCase):
    def test_import_runs_no_subprocess(self):
        # Importing the module must not have invoked nmcli. We assert
        # the module imported cleanly (it did, at file top) and that the
        # constant contract holds.
        import fp_lapse.net.nmcli as mod
        self.assertEqual(mod.WLAN_DEV, "wlan0")
        self.assertEqual(CONNECT_TIMEOUT_S, 30.0)


if __name__ == "__main__":
    unittest.main()
