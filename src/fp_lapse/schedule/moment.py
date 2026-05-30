"""`ScheduledMoment` — pure data model for a per-config start/end moment.

A moment is one of two shapes (decision #1 in `concept.md`):

- `time` only, `date is None`: **daily recurrence**. Fires every day at
  that time of day, indefinitely.
- `time` and `date` set: **one-shot absolute datetime**. Fires once,
  exactly at `datetime.combine(date, time)`.

The forbidden combination — `date` set but `time` absent — is
unrepresentable because `time` is mandatory at construction.

All times are **Pi local time** (naive `datetime`), per the project's
no-time-zone-selection design (concept §Non-goals).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_t, datetime, time as time_t, timedelta
from typing import Optional


@dataclass(frozen=True)
class ScheduledMoment:
    """A single scheduled firing instant — daily or one-shot.

    See module docstring for the two valid shapes. `time` is
    mandatory; passing `time=None` raises `ValueError`.
    """

    time: time_t
    date: Optional[date_t] = None

    def __post_init__(self) -> None:
        if self.time is None:
            raise ValueError("ScheduledMoment requires a non-None time")
        if not isinstance(self.time, time_t):
            raise ValueError(
                f"ScheduledMoment.time must be datetime.time, got {type(self.time).__name__}"
            )
        if self.date is not None and not isinstance(self.date, date_t):
            raise ValueError(
                f"ScheduledMoment.date must be datetime.date or None, got {type(self.date).__name__}"
            )

    # ------------------------------------------------------------------
    # Firing helpers
    # ------------------------------------------------------------------
    def next_firing(self, now: datetime) -> Optional[datetime]:
        """The next firing datetime at or after `now`.

        - One-shot (`date is not None`): the combined `(date, time)` if
          it is still in the future (or exactly `now`), else `None`
          (one-shot in the past never fires again).
        - Daily (`date is None`): today's instance if `time >= now.time()`
          on the same day; otherwise tomorrow's instance.
        """
        if self.date is not None:
            fire = datetime.combine(self.date, self.time)
            return fire if fire >= now else None
        # Daily: try today; if already passed, tomorrow.
        today_fire = datetime.combine(now.date(), self.time)
        if today_fire >= now:
            return today_fire
        return today_fire + timedelta(days=1)

    def events_in(
        self,
        start_exclusive: datetime,
        end_inclusive: datetime,
    ) -> list[datetime]:
        """Every firing datetime in `(start_exclusive, end_inclusive]`.

        - Backward intervals (`end < start`) return `[]`.
        - One-shot: 0 or 1 element depending on whether the firing
          falls strictly after `start_exclusive` and at-or-before
          `end_inclusive`.
        - Daily: every per-day instance in the interval. Typical case
          is 0 or 1 element (1 Hz UI cadence); a forward jump crossing
          multiple days returns all of them in chronological order.
        """
        if end_inclusive < start_exclusive:
            return []
        if self.date is not None:
            fire = datetime.combine(self.date, self.time)
            if start_exclusive < fire <= end_inclusive:
                return [fire]
            return []
        # Daily: iterate per day from start_exclusive.date() forward.
        out: list[datetime] = []
        day = start_exclusive.date()
        # Anchor: first candidate is today's instance.
        # We need to enumerate enough days to cover the span.
        while day <= end_inclusive.date():
            fire = datetime.combine(day, self.time)
            if start_exclusive < fire <= end_inclusive:
                out.append(fire)
            day = day + timedelta(days=1)
        return out
