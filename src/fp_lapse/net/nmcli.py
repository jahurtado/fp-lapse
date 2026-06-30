"""Thin `nmcli` wrapper for the on-device Wi-Fi setup flow.

Modelled on `fp_lapse.shutdown`: fixed argv, no shell, the subprocess
boundary lives behind an injectable `runner` so the pure parsing logic
is 100 % unit-testable on a Mac with no `nmcli` present. Importing this
module runs **no** subprocess.

Layers:

  - dataclasses / enums (`WifiNetwork`, `WifiStatus`, `ConnectResult`,
    `ConnectOutcome`) — plain data the App holds.
  - pure parse helpers (`parse_scan` / `parse_saved` / `parse_status` /
    `classify_connect` / `signal_glyph`) — string in, data out.
  - subprocess-backed fns (`scan` / `connect` / `status` / `forget`) —
    lazy; only these touch `nmcli`. `connect` carries the 30 s timeout.
  - facades (`NmcliFacade` real, `MockNmcli` canned) — the object the
    App holds; `make_nmcli(use_mock=...)` picks one.

`nmcli` needs root; `fp-lapse.service` already runs as root so no `sudo`
and no polkit rule is needed.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


WLAN_DEV: str = "wlan0"
CONNECT_TIMEOUT_S: float = 30.0

# A runner takes the argv list and an optional timeout and returns
# `(returncode, stdout, stderr)`. The default shells out via
# `subprocess.run`; tests inject a fake returning canned tuples.
Runner = Callable[..., Tuple[int, str, str]]


# ----------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int          # 0..100 (nmcli SIGNAL)
    secured: bool        # SECURITY non-empty (WPA/WPA2/WPA3/WEP)
    active: bool         # IN-USE "*"
    saved: bool          # ssid matches a saved NM profile name


@dataclass(frozen=True)
class WifiStatus:
    connection: Optional[str]   # GENERAL.CONNECTION ("--" → None)
    ip: Optional[str]           # first IP4.ADDRESS, CIDR stripped


class ConnectOutcome(str, Enum):
    SUCCESS = "success"        # returncode 0
    BAD_AUTH = "bad_auth"      # secrets / 802-11-wireless-security
    NOT_FOUND = "not_found"    # no network with that SSID / out of range
    TIMEOUT = "timeout"        # subprocess.TimeoutExpired (>30 s)
    FAILED = "failed"          # any other non-zero exit


@dataclass(frozen=True)
class ConnectResult:
    outcome: ConnectOutcome
    ssid: str
    detail: Optional[str] = None   # raw stderr tail for the log / status line


# ----------------------------------------------------------------------
# Pure parse helpers (no subprocess; Mac-unit-tested)
# ----------------------------------------------------------------------


def _split_terse(line: str) -> List[str]:
    """Split one nmcli `-t` terse line on unescaped `:`.

    nmcli backslash-escapes literal `:` (→ `\\:`) and `\\` (→ `\\\\`)
    inside fields; unescape both while splitting.
    """
    fields: List[str] = []
    cur: List[str] = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\" and i + 1 < n:
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            fields.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    fields.append("".join(cur))
    return fields


def parse_scan(stdout: str, *, saved: Set[str]) -> List[WifiNetwork]:
    """Build the `WifiNetwork` list from `device wifi list` terse output.

    Dedupes by ssid (keeps max signal, OR's the active flag), drops blank
    SSIDs, and sorts active-first then signal descending.
    """
    by_ssid: dict[str, WifiNetwork] = {}
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        parts = _split_terse(raw)
        if len(parts) < 4:
            continue
        in_use, ssid, signal_s, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue
        try:
            signal = int(signal_s)
        except ValueError:
            signal = 0
        active = in_use.strip() == "*"
        secured = bool(security.strip())
        existing = by_ssid.get(ssid)
        if existing is None:
            by_ssid[ssid] = WifiNetwork(
                ssid=ssid, signal=signal, secured=secured,
                active=active, saved=ssid in saved,
            )
        else:
            by_ssid[ssid] = WifiNetwork(
                ssid=ssid,
                signal=max(existing.signal, signal),
                secured=existing.secured or secured,
                active=existing.active or active,
                saved=existing.saved,
            )
    nets = list(by_ssid.values())
    nets.sort(key=lambda w: (not w.active, -w.signal))
    return nets


def parse_saved(stdout: str) -> Set[str]:
    """Names of wifi-type saved profiles from `connection show` output."""
    saved: Set[str] = set()
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        parts = _split_terse(raw)
        if len(parts) < 2:
            continue
        name, type_ = parts[0], parts[1]
        if "wireless" in type_:
            saved.add(name)
    return saved


def parse_status(stdout: str) -> WifiStatus:
    """GENERAL.CONNECTION + first IP4.ADDRESS (strip `/CIDR`)."""
    connection: Optional[str] = None
    ip: Optional[str] = None
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        parts = _split_terse(raw)
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1]
        if key == "GENERAL.CONNECTION":
            connection = None if value in ("", "--") else value
        elif key.startswith("IP4.ADDRESS") and ip is None:
            ip = value.split("/")[0] or None
    return WifiStatus(connection=connection, ip=ip)


# stderr substrings (case-insensitive) → outcome. Checked in order; the
# generic FAILED catch-all covers anything else with a non-zero exit.
_BAD_AUTH_MARKERS = (
    "secrets were required",
    "802-11-wireless-security",
    "no secrets",
)
_NOT_FOUND_MARKERS = (
    "no network with ssid",
    "not found",
    "no suitable",
)


def classify_connect(returncode: int, stderr: str) -> ConnectOutcome:
    """Map an nmcli connect exit code + stderr to a `ConnectOutcome`.

    `TIMEOUT` is NOT produced here — it is raised by the subprocess and
    handled in `connect()`; this function only sees a completed run.
    """
    if returncode == 0:
        return ConnectOutcome.SUCCESS
    low = stderr.lower()
    if any(m in low for m in _BAD_AUTH_MARKERS):
        return ConnectOutcome.BAD_AUTH
    if any(m in low for m in _NOT_FOUND_MARKERS):
        return ConnectOutcome.NOT_FOUND
    return ConnectOutcome.FAILED


def signal_glyph(signal: int) -> str:
    """4-tier fixed-width (3-char) signal bar."""
    if signal < 25:
        return "·  "
    if signal < 50:
        return "▮  "
    if signal < 75:
        return "▮▮ "
    return "▮▮▮"


# ----------------------------------------------------------------------
# Subprocess-backed functions (lazy; Pi/root)
# ----------------------------------------------------------------------


def _default_runner(argv: List[str], *, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    """Default subprocess boundary: `subprocess.run` with captured output.

    A non-zero exit is NOT raised — the caller inspects the returncode +
    stderr. `subprocess.TimeoutExpired` DOES propagate (caught by
    `connect()` for the 30 s budget).
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        argv, capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def saved_profiles(*, runner: Runner = _default_runner) -> Set[str]:
    """Set of saved wifi profile names (`connection show`)."""
    _rc, out, _err = runner(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
    )
    return parse_saved(out)


def scan(*, rescan: bool = False, runner: Runner = _default_runner) -> List[WifiNetwork]:
    """List nearby Wi-Fi networks, enriched with the `saved` flag.

    `rescan=True` forces a fresh `--rescan yes` (slower, can briefly
    disrupt the active link) — used only by the explicit Rescan action.
    """
    argv = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
            "device", "wifi", "list"]
    if rescan:
        argv += ["--rescan", "yes"]
    _rc, out, _err = runner(argv)
    saved = saved_profiles(runner=runner)
    return parse_scan(out, saved=saved)


def connect(
    ssid: str,
    password: Optional[str] = None,
    *,
    hidden: bool = False,
    runner: Runner = _default_runner,
) -> ConnectResult:
    """Join `ssid`, blocking until associated or error (30 s timeout).

    The timeout lives here (inside the subprocess call) so the App worker
    needs no separate watchdog.
    """
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        argv += ["password", password]
    if hidden:
        argv += ["hidden", "yes"]
    try:
        rc, _out, err = runner(argv, timeout=CONNECT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("nmcli connect '%s' timed out after %.0fs",
                       ssid, CONNECT_TIMEOUT_S)
        return ConnectResult(
            ConnectOutcome.TIMEOUT, ssid,
            f"Timed out after {int(CONNECT_TIMEOUT_S)} s",
        )
    outcome = classify_connect(rc, err or "")
    detail: Optional[str] = None
    if outcome is not ConnectOutcome.SUCCESS:
        tail = (err or "").strip().splitlines()
        detail = tail[-1] if tail else None
        logger.warning("nmcli connect '%s' → %s (%s)", ssid, outcome.value, detail)
    return ConnectResult(outcome, ssid, detail)


def status(*, runner: Runner = _default_runner) -> WifiStatus:
    """Active connection name + first IPv4 address on `wlan0`."""
    _rc, out, _err = runner(
        ["nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
         "device", "show", WLAN_DEV],
    )
    return parse_status(out)


def forget(ssid: str, *, runner: Runner = _default_runner) -> bool:
    """Delete the saved NM profile named `ssid`. True on success."""
    rc, _out, err = runner(["nmcli", "connection", "delete", ssid])
    if rc != 0:
        logger.warning("nmcli connection delete '%s' failed: %s", ssid, err)
    return rc == 0


# ----------------------------------------------------------------------
# Facades — the object the App holds (injectable for tests)
# ----------------------------------------------------------------------


class NmcliFacade:
    """Real facade: each method calls the module function with `runner`."""

    def __init__(self, *, runner: Runner = _default_runner) -> None:
        self._runner = runner

    def scan(self, *, rescan: bool = False) -> List[WifiNetwork]:
        return scan(rescan=rescan, runner=self._runner)

    def connect(
        self, ssid: str, password: Optional[str] = None, *, hidden: bool = False,
    ) -> ConnectResult:
        return connect(ssid, password, hidden=hidden, runner=self._runner)

    def status(self) -> WifiStatus:
        return status(runner=self._runner)

    def forget(self, ssid: str) -> bool:
        return forget(ssid, runner=self._runner)


class MockNmcli:
    """Canned facade for the Mac dev harness (no `nmcli`, no hardware).

    `scan()` returns a small fixed list (one active/secured/saved AP, an
    open AP, a couple of others); `connect()` succeeds unless the magic
    password `"badpass"` is used (to exercise the failure screen);
    `forget()` removes the network from subsequent scans.
    """

    # Typeable on the keyboard (≥ 8 chars) so the dev harness can
    # actually reach the failure screen.
    _MAGIC_BAD_PASSWORD = "wrongpass"

    def __init__(self, *, connect_delay_s: float = 0.0) -> None:
        self._connect_delay_s = connect_delay_s
        self._forgotten: Set[str] = set()
        self._connected_ssid: str = "MyHomeWiFi"
        self._all = [
            WifiNetwork("MyHomeWiFi", 72, secured=True, active=True, saved=True),
            WifiNetwork("Guest_Network", 55, secured=True, active=False, saved=False),
            WifiNetwork("CoffeeShop", 48, secured=False, active=False, saved=False),
            WifiNetwork("Router_5G", 30, secured=True, active=False, saved=False),
            WifiNetwork("Cabin_5G", 12, secured=True, active=False, saved=False),
        ]

    def scan(self, *, rescan: bool = False) -> List[WifiNetwork]:
        return [n for n in self._all if n.ssid not in self._forgotten]

    def connect(
        self, ssid: str, password: Optional[str] = None, *, hidden: bool = False,
    ) -> ConnectResult:
        if self._connect_delay_s > 0:
            import time
            time.sleep(self._connect_delay_s)
        if password == self._MAGIC_BAD_PASSWORD:
            return ConnectResult(
                ConnectOutcome.BAD_AUTH, ssid,
                "Secrets were required, but not provided.",
            )
        self._connected_ssid = ssid
        return ConnectResult(ConnectOutcome.SUCCESS, ssid)

    def status(self) -> WifiStatus:
        return WifiStatus(self._connected_ssid, "192.168.1.42")

    def forget(self, ssid: str) -> bool:
        self._forgotten.add(ssid)
        return True


def make_nmcli(*, use_mock: bool, connect_delay_s: float = 0.0):
    """Pick the facade: `MockNmcli` on the Mac dev path, real otherwise."""
    if use_mock:
        return MockNmcli(connect_delay_s=connect_delay_s)
    return NmcliFacade()
