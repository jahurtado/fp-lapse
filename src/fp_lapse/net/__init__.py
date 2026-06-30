"""Network configuration layer (wifi-manual-config feature).

A thin, import-safe wrapper around the `nmcli` CLI used by the on-device
Wi-Fi setup flow. Like `fp_lapse.camera`, the subprocess-touching code is
lazy: `import fp_lapse.net.nmcli` runs no subprocess and is safe on a
vanilla Mac with no `nmcli` installed.
"""

from .nmcli import (
    CONNECT_TIMEOUT_S,
    WLAN_DEV,
    ConnectOutcome,
    ConnectResult,
    MockNmcli,
    NmcliFacade,
    WifiNetwork,
    WifiStatus,
    classify_connect,
    connect,
    forget,
    make_nmcli,
    parse_saved,
    parse_scan,
    parse_status,
    saved_profiles,
    scan,
    signal_glyph,
    status,
)

__all__ = [
    "CONNECT_TIMEOUT_S",
    "WLAN_DEV",
    "ConnectOutcome",
    "ConnectResult",
    "MockNmcli",
    "NmcliFacade",
    "WifiNetwork",
    "WifiStatus",
    "classify_connect",
    "connect",
    "forget",
    "make_nmcli",
    "parse_saved",
    "parse_scan",
    "parse_status",
    "saved_profiles",
    "scan",
    "signal_glyph",
    "status",
]
