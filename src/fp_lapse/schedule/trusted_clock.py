"""`TrustedClock` — concept decision #10 in code.

The schedule engine does NOT read `datetime.now()` directly. Instead
it consults this trusted clock, which maintains a
`(baseline_monotonic, baseline_wall)` pair anchored on a verified NTP
sync. The trusted "now" is `baseline_wall + (monotonic - baseline_mono)`
seconds — immune to subsequent OS-clock disturbances.

NTP updates are only accepted into the baseline when they fall within
an envelope around the engine's own predicted time, scaled with how
long the baseline has been running:

    tolerance_s = max(BASE_TOLERANCE_S,
                      DRIFT_PPM_AS_FRACTION * baseline_age_s + SAFETY_S)

That is `max(5 s, 100 ppm × age + 1 s)` — above the Pi 3's worst-case
drift, well below "this is clearly wrong". A 4-hour jump never
qualifies as fine-tuning regardless of age.

**Force-trust** is the operator escape hatch: a single call to
`force_trust_next_sync()` makes the next `on_sync_observed(...)` accept
its proposed wall time unconditionally (and clear `is_glitched`).

Thread-safety: this class is intentionally **not internally locked**.
The two-field reads in `now()` (`_baseline_mono`, `_baseline_wall`) are
GIL-atomic individually but can race against a concurrent mutator. The
worst observable behaviour is one schedule tick reading an
`(old_mono, new_wall)` pair worth a few milliseconds of skew — well
inside the schedule's tick cadence. The App is the only intended
mutator and acquires `app.lock` around `on_sync_observed`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SyncOutcome(str, Enum):
    """Result of a single `TrustedClock.on_sync_observed(...)` call."""

    FIRST_SYNC = "first_sync"   # baseline freshly anchored
    ACCEPTED = "accepted"        # within envelope; baseline updated
    REJECTED = "rejected"        # outside envelope; baseline held
    FORCED = "forced"            # force_trust flag honoured


class TrustedClock:
    BASE_TOLERANCE_S: float = 5.0
    DRIFT_PPM_AS_FRACTION: float = 100e-6   # 100 ppm
    SAFETY_S: float = 1.0

    def __init__(
        self,
        *,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now_monotonic = now_monotonic
        self._baseline_mono: Optional[float] = None
        self._baseline_wall: Optional[datetime] = None
        self._force_pending: bool = False
        self._is_glitched: bool = False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @property
    def has_baseline(self) -> bool:
        return self._baseline_mono is not None

    @property
    def is_glitched(self) -> bool:
        """True iff the most recent sync was REJECTED.

        Cleared on the next ACCEPTED / FORCED / FIRST_SYNC.
        """
        return self._is_glitched

    def now(self) -> Optional[datetime]:
        """Trusted wall time. `None` if no baseline yet."""
        if self._baseline_mono is None or self._baseline_wall is None:
            return None
        delta = self._now_monotonic() - self._baseline_mono
        return self._baseline_wall + timedelta(seconds=delta)

    def tolerance_s(self) -> float:
        """Current envelope size — for logging / debugging."""
        if self._baseline_mono is None:
            return self.BASE_TOLERANCE_S
        age = self._now_monotonic() - self._baseline_mono
        return max(
            self.BASE_TOLERANCE_S,
            self.DRIFT_PPM_AS_FRACTION * age + self.SAFETY_S,
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def force_trust_next_sync(self) -> None:
        """Operator action (LEFT button).

        The next `on_sync_observed(...)` will return `FORCED` and
        unconditionally adopt the proposed wall time. The flag is
        cleared by that call.
        """
        self._force_pending = True
        logger.info("trusted_clock: force-trust flag armed")

    def on_sync_observed(self, os_wall_now: datetime) -> SyncOutcome:
        """Process an observed OS-level sync.

        Updates the baseline iff the proposal passes the envelope (or
        the force flag is set, or there is no baseline yet).
        """
        mono = self._now_monotonic()

        if self._force_pending:
            self._baseline_mono = mono
            self._baseline_wall = os_wall_now
            self._force_pending = False
            self._is_glitched = False
            logger.info(
                "trusted_clock: outcome=%s proposed=%s",
                SyncOutcome.FORCED.value, os_wall_now.isoformat(),
            )
            return SyncOutcome.FORCED

        if self._baseline_mono is None or self._baseline_wall is None:
            self._baseline_mono = mono
            self._baseline_wall = os_wall_now
            self._is_glitched = False
            logger.info(
                "trusted_clock: outcome=%s proposed=%s",
                SyncOutcome.FIRST_SYNC.value, os_wall_now.isoformat(),
            )
            return SyncOutcome.FIRST_SYNC

        predicted = self._baseline_wall + timedelta(
            seconds=mono - self._baseline_mono
        )
        delta_s = abs((os_wall_now - predicted).total_seconds())
        tol = self.tolerance_s()
        age = mono - self._baseline_mono

        if delta_s <= tol:
            # Absorb the legitimate fine-tuning.
            self._baseline_mono = mono
            self._baseline_wall = os_wall_now
            self._is_glitched = False
            logger.info(
                "trusted_clock: outcome=%s predicted=%s proposed=%s "
                "delta_s=%.3f tolerance_s=%.3f age_s=%.1f",
                SyncOutcome.ACCEPTED.value,
                predicted.isoformat(), os_wall_now.isoformat(),
                delta_s, tol, age,
            )
            return SyncOutcome.ACCEPTED

        self._is_glitched = True
        logger.warning(
            "trusted_clock: outcome=%s predicted=%s proposed=%s "
            "delta_s=%.3f tolerance_s=%.3f age_s=%.1f",
            SyncOutcome.REJECTED.value,
            predicted.isoformat(), os_wall_now.isoformat(),
            delta_s, tol, age,
        )
        return SyncOutcome.REJECTED
