"""Schedule layer: data model, persistence, time-sync prober, trusted
clock, schedule evaluator.

Backend half of the `scheduled-configs` feature. No threads of its own;
the prober and the evaluator are pure objects driven inline from the UI
main loop.

See `docs/features/scheduled-configs/prd.md` and `concept.md` for the
design and decisions.
"""

from __future__ import annotations

from .moment import ScheduledMoment
from .schedule_evaluator import ScheduleEvaluator
from .store import ScheduleStateStore
from .time_sync_prober import TimeSyncProber
from .trusted_clock import SyncOutcome, TrustedClock

# Threshold above which a successful sync is considered "stale" by the
# UI (decision #2). The schedule engine keeps firing on the trusted
# baseline regardless — this is a UI hint only.
SCHEDULE_STALE_THRESHOLD_S: float = 2 * 60 * 60   # 2 hours

__all__ = [
    "ScheduledMoment",
    "ScheduleEvaluator",
    "ScheduleStateStore",
    "TimeSyncProber",
    "TrustedClock",
    "SyncOutcome",
    "SCHEDULE_STALE_THRESHOLD_S",
]
