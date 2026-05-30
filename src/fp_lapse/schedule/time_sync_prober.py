"""`TimeSyncProber` — inline-driven NTP-sync detector.

This object has **no thread**. The UI main loop calls `maybe_poll()`
on every iteration; the prober internally gates calls so the actual
`timedatectl` subprocess runs at most every `poll_interval_s` seconds
(default 60).

Detection logic per poll:

1. Run `timedatectl show -p NTPSynchronized -p TimeUSec --value`.
2. Parse the two newline-separated tokens (`yes`/`no` and the
   `TimeUSec` string).
3. If `NTPSynchronized == "yes"`:
   - First time this boot → record + fire `on_sync(now_wall())`.
   - Subsequent times → if `TimeUSec` has changed, fire `on_sync` again
     (timesyncd updates `TimeUSec` after each successful sync).
4. If `NTPSynchronized == "no"`, do nothing — once synced we never
   un-sync (decision #2).

`request_force_sync()` triggers the OS-level sync nudge (a quick
`timedatectl set-ntp false` / `true` toggle) so the next `maybe_poll`
can observe a fresh sync. It does NOT set the trusted-clock force
flag — that lives on `TrustedClock.force_trust_next_sync()` and is the
caller's responsibility to set before calling `request_force_sync()`.

A small `threading.RLock` is kept around the state set as defense in
depth + forward compatibility — if some future thread starts calling
`maybe_poll()`, the prober stays safe. There is no background thread,
no `start()` / `shutdown()`.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _default_runner(cmd: list[str]) -> str:
    """Default timedatectl runner: subprocess.check_output with a timeout."""
    return subprocess.check_output(cmd, text=True, timeout=5)


def _default_force_sync() -> None:
    """Nudge systemd-timesyncd to perform a fresh NTP sync.

    Toggling `set-ntp off` then `set-ntp on` is the canonical way to
    force a fresh sync attempt against the configured upstream pool.
    Failures are logged at WARNING; never raised — the caller (UI
    button handler) treats this as a best-effort nudge.
    """
    try:
        subprocess.run(
            ["timedatectl", "set-ntp", "false"],
            check=True, timeout=5,
        )
        subprocess.run(
            ["timedatectl", "set-ntp", "true"],
            check=True, timeout=5,
        )
    except Exception as e:  # pragma: no cover - exercised via mock
        logger.warning("force_sync subprocess failed: %s", e)


class TimeSyncProber:
    DEFAULT_POLL_INTERVAL_S: float = 60.0

    def __init__(
        self,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        on_sync: Optional[Callable[[datetime], None]] = None,
        timedatectl_runner: Callable[[list[str]], str] = _default_runner,
        force_sync_runner: Callable[[], None] = _default_force_sync,
        now_wall: Callable[[], datetime] = datetime.now,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._on_sync = on_sync
        self._runner = timedatectl_runner
        self._force_sync_runner = force_sync_runner
        self._now_wall = now_wall
        self._now_monotonic = now_monotonic

        self._lock = threading.RLock()
        self._is_synced: bool = False
        self._last_sync_mono: Optional[float] = None
        self._last_sync_wall: Optional[datetime] = None
        self._last_observed_time_usec: Optional[str] = None
        self._last_poll_mono: Optional[float] = None

    # ------------------------------------------------------------------
    # Inline-driven entrypoint
    # ------------------------------------------------------------------
    def maybe_poll(self) -> None:
        """Run a single poll if enough monotonic time has elapsed."""
        now_m = self._now_monotonic()
        with self._lock:
            if (
                self._last_poll_mono is not None
                and (now_m - self._last_poll_mono) < self._poll_interval_s
            ):
                return
            self._last_poll_mono = now_m
        self._do_poll()

    def force_poll(self) -> None:
        """Poll immediately, bypassing the cadence gate (addendum A1).

        Used by `App._run_sync_worker` right after the force-sync
        subprocess returns so the freshly-anchored OS clock is observed
        without waiting up to 60 s for the next routine `maybe_poll()`.
        Updates `_last_poll_mono` so a `maybe_poll()` called immediately
        after is still gated as expected.
        """
        with self._lock:
            self._last_poll_mono = self._now_monotonic()
        self._do_poll()

    def _do_poll(self) -> None:
        """Body shared by `maybe_poll` (gated) and `force_poll` (ungated)."""
        try:
            raw = self._runner(
                ["timedatectl", "show", "-p", "NTPSynchronized",
                 "-p", "TimeUSec", "--value"]
            )
        except Exception as e:
            logger.warning("time_sync_prober: runner raised: %s", e)
            return

        synced, time_usec = self._parse(raw)
        if not synced:
            # Once synced we never un-sync (decision #2); but we also
            # never claim a sync we didn't observe.
            return

        wall_now = self._now_wall()
        fresh = False
        with self._lock:
            if not self._is_synced:
                # First sync this boot.
                self._is_synced = True
                self._last_sync_mono = self._now_monotonic()
                self._last_sync_wall = wall_now
                self._last_observed_time_usec = time_usec
                fresh = True
                logger.info(
                    "time_sync_prober: first sync this boot at %s (TimeUSec=%s)",
                    wall_now.isoformat(), time_usec,
                )
            elif time_usec != self._last_observed_time_usec:
                # Fresh sync (TimeUSec advanced).
                self._last_sync_mono = self._now_monotonic()
                self._last_sync_wall = wall_now
                self._last_observed_time_usec = time_usec
                fresh = True
                logger.info(
                    "time_sync_prober: fresh sync observed at %s (TimeUSec=%s)",
                    wall_now.isoformat(), time_usec,
                )

        if fresh and self._on_sync is not None:
            try:
                self._on_sync(wall_now)
            except Exception:
                logger.exception("time_sync_prober: on_sync callback raised")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def is_synced(self) -> bool:
        with self._lock:
            return self._is_synced

    def last_successful_sync_at_monotonic(self) -> Optional[float]:
        with self._lock:
            return self._last_sync_mono

    def last_successful_sync_wall(self) -> Optional[datetime]:
        with self._lock:
            return self._last_sync_wall

    # ------------------------------------------------------------------
    # Operator action
    # ------------------------------------------------------------------
    def request_force_sync(self) -> None:
        """Trigger an OS-level NTP sync nudge.

        Returns immediately. Failures in the runner are swallowed
        (logged at WARNING). The next `maybe_poll()` will see whatever
        sync the OS produced.
        """
        logger.info("time_sync_prober: force-sync requested")
        try:
            self._force_sync_runner()
        except Exception as e:
            logger.warning("time_sync_prober: force_sync_runner raised: %s", e)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse(raw: str) -> tuple[bool, str]:
        """Parse the two-line `timedatectl show --value` output.

        Returns `(is_synced, time_usec)`. On malformed input both
        defaults — `(False, "")` — are returned so the caller treats
        it as "no sync this poll".
        """
        if not raw:
            return False, ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return False, ""
        synced = lines[0].lower() == "yes"
        time_usec = lines[1] if len(lines) >= 2 else ""
        return synced, time_usec
