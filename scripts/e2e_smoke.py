#!/usr/bin/env python3
"""End-to-end smoke test of the running fp-lapse service.

Drives the live app through the HTTP control server
(`FP_LAPSE_CONTROL=1`, default localhost:9999) — navigates to a
config, starts the timelapse, waits N seconds, asserts shots/skips
are within bounds, then stops the engine.

Stdlib-only (urllib + argparse + json). Runs from the Pi itself or
over SSH from the Mac.

Examples:

    # On the Pi, default Config 1, 25 s, expect at least 4 shots and 0 skips
    python3 scripts/e2e_smoke.py --min-shots 4

    # From a dev box via SSH (Pi already running fp-lapse.service)
    ssh <pi-host> 'python3 ~/fp-lapse/scripts/e2e_smoke.py \\
        --config "Fast 3x 1/50" --seconds 15 --min-shots 6'

    # Also read TICK lateness from the journal (Pi only)
    python3 scripts/e2e_smoke.py --journal --max-lateness-ms 50

Exit code: 0 on PASS, 1 on FAIL (bad shots/skips/lateness), 2 on
infrastructure error (control server unreachable, missing config).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _http(base: str, path: str, *, method: str = "GET", timeout: float = 5.0) -> dict:
    req = urllib.request.Request(f"{base}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _state(base: str) -> dict:
    return _http(base, "/state")


def _tap(base: str, btn: str) -> None:
    _http(base, f"/tap/{btn}", method="POST")


def _move_cursor_to(base: str, target_idx: int, max_taps: int = 12) -> dict:
    """Walks `main_cursor` to `target_idx` with UP/DOWN taps.

    Returns the state snapshot once the cursor matches.
    """
    state = _state(base)
    for _ in range(max_taps):
        cursor = state["ui"]["main_cursor"]
        if cursor == target_idx:
            return state
        _tap(base, "DOWN" if cursor < target_idx else "UP")
        time.sleep(0.2)
        state = _state(base)
    raise RuntimeError(
        f"could not reach cursor {target_idx} after {max_taps} taps "
        f"(stuck at {state['ui']['main_cursor']})"
    )


def _find_config_index(state: dict, name: str) -> int | None:
    for i, c in enumerate(state["configs"]):
        if c["name"] == name:
            return i
    return None


def _journal_lateness_ms(since_iso: str) -> list[int]:
    """Read engine TICK lateness values (in ms) from the journal since
    the given timestamp. Returns an empty list if journalctl isn't
    available or yields no matching lines.
    """
    try:
        out = subprocess.check_output(
            [
                "journalctl", "-u", "fp-lapse.service",
                "--since", since_iso, "--no-pager", "-o", "cat",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="replace")
    except Exception:
        return []
    # Match e.g.  "engine: TICK k=0 shots=2 lateness=+0.006s"
    pattern = re.compile(r"engine: TICK .* lateness=([+-]?[\d.]+)s")
    values: list[int] = []
    for line in out.splitlines():
        m = pattern.search(line)
        if m:
            values.append(int(round(float(m.group(1)) * 1000)))
    return values


def _fmt_state(s: dict) -> str:
    e = s["engine"]
    return (
        f"engine={e['state']:<7} "
        f"shots={e['shots_taken']:3d} "
        f"skips={e['skips']:2d} "
        f"next_in={(e['seconds_to_next_shot'] or 0):.1f}s "
        f"screen={s['ui']['screen']}"
    )


def run(args: argparse.Namespace) -> int:
    base = f"http://{args.host}:{args.port}"
    started_iso = time.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[setup] connecting to {base}")
    try:
        state = _state(base)
    except (urllib.error.URLError, OSError) as e:
        print(f"FAIL: control server unreachable: {e}")
        return 2

    print(f"[setup] {_fmt_state(state)}")
    if state["engine"]["state"] != "idle":
        print(
            f"FAIL: engine is {state['engine']['state']!r}, expected idle. "
            "Stop the running timelapse before retrying."
        )
        return 2

    if not state["configs"]:
        print("FAIL: no configs available on the device")
        return 2

    target_name = args.config or state["configs"][0]["name"]
    target_idx = _find_config_index(state, target_name)
    if target_idx is None:
        names = [c["name"] for c in state["configs"]]
        print(f"FAIL: config {target_name!r} not found; available: {names}")
        return 2
    cfg = state["configs"][target_idx]
    interval = cfg["interval_s"]
    shots_per_bracket = len(cfg["shots"])
    expected_brackets = int(args.seconds // interval) + 1  # k=0 fires ASAP
    expected_shots = expected_brackets * shots_per_bracket

    print(
        f"[setup] target config={target_name!r} interval={interval}s "
        f"shots/bracket={shots_per_bracket} → expected ≥{expected_shots} shots "
        f"in {args.seconds}s"
    )

    try:
        state = _move_cursor_to(base, target_idx)
    except RuntimeError as e:
        print(f"FAIL: {e}")
        return 2
    print(f"[ready] {_fmt_state(state)}")

    # Start
    _tap(base, "OK")
    time.sleep(0.5)
    state = _state(base)
    if state["engine"]["state"] != "running":
        print(f"FAIL: engine did not start: {_fmt_state(state)}")
        return 1
    print(f"[start] {_fmt_state(state)}")

    # Drive for the requested duration with periodic snapshots.
    t0 = time.monotonic()
    next_print = t0 + 5.0
    while time.monotonic() - t0 < args.seconds:
        time.sleep(0.5)
        now = time.monotonic()
        if now >= next_print:
            state = _state(base)
            print(f"[t+{now - t0:5.1f}s] {_fmt_state(state)}")
            next_print = now + 5.0

    state = _state(base)
    final_shots = state["engine"]["shots_taken"]
    final_skips = state["engine"]["skips"]
    fails = state["engine"]["consecutive_failures"]
    print(
        f"[done]  shots={final_shots} skips={final_skips} "
        f"consecutive_failures={fails}"
    )

    # Stop (BACK opens overlay, OK confirms).
    _tap(base, "BACK")
    time.sleep(0.3)
    _tap(base, "OK")
    time.sleep(0.5)
    state = _state(base)
    stopped_clean = state["engine"]["state"] == "idle"
    print(f"[stop]  {_fmt_state(state)}")

    # Optional: read journal for TICK lateness.
    lateness_ms: list[int] = []
    if args.journal:
        lateness_ms = _journal_lateness_ms(started_iso)
        if lateness_ms:
            print(
                f"[journal] {len(lateness_ms)} TICK lines, "
                f"lateness ms: min={min(lateness_ms)} "
                f"max={max(lateness_ms)} avg={sum(lateness_ms) // len(lateness_ms)}"
            )
        else:
            print("[journal] no TICK lines (need sudo? skipping check)")

    # Verdict.
    issues: list[str] = []
    if final_shots < args.min_shots:
        issues.append(f"shots {final_shots} < min {args.min_shots}")
    if final_skips > args.max_skips:
        issues.append(f"skips {final_skips} > max {args.max_skips}")
    if not stopped_clean:
        issues.append(f"engine did not stop cleanly: state={state['engine']['state']!r}")
    if args.journal and lateness_ms:
        max_seen = max(lateness_ms)
        if max_seen > args.max_lateness_ms:
            issues.append(
                f"lateness peak {max_seen} ms > max {args.max_lateness_ms} ms"
            )

    if issues:
        print(f"FAIL: {'; '.join(issues)}")
        return 1
    print(f"PASS: shots={final_shots} skips={final_skips}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end smoke test against a running fp-lapse service.",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument(
        "--config", default=None,
        help="config name (default: first one returned by the device)",
    )
    ap.add_argument(
        "--seconds", type=float, default=25.0,
        help="how long to keep the engine running (default 25)",
    )
    ap.add_argument(
        "--min-shots", type=int, default=1,
        help="minimum successful shots expected (default 1)",
    )
    ap.add_argument(
        "--max-skips", type=int, default=0,
        help="maximum tolerated SKIP count (default 0)",
    )
    ap.add_argument(
        "--journal", action="store_true",
        help="also read journalctl for TICK lateness (Pi-only)",
    )
    ap.add_argument(
        "--max-lateness-ms", type=int, default=50,
        help="max lateness (ms) tolerated when --journal is on (default 50)",
    )
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
