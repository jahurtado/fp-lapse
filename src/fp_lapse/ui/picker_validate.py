"""Pure helper: validate a tuple of digit-cells parsed as date/time.

Used by the digit picker (`picker_datetime.py`) to decide whether the
current set of digits constitutes a valid `ScheduledMoment`. Separated
into its own module so the validation table can be unit-tested without
any rendering / interaction dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_t, time as time_t
from typing import Optional


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a picker validation pass."""

    ok: bool
    error: Optional[str] = None
    date: Optional[date_t] = None
    time: Optional[time_t] = None


# Lower bound for year — chosen for sanity. The eclipse target is 2026.
MIN_YEAR: int = 2000
# Sanity cap (no Pi 3 will be running this in the 22nd century, but if
# it does, the cap is here to prevent four-digit overflow / nonsensical
# years in the picker UI).
MAX_YEAR: int = 2099


def _days_in_month(year: int, month: int) -> int:
    """Days in `(year, month)`. Caller has already validated `1<=month<=12`."""
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    # February — leap year if divisible by 4, except centuries unless by 400.
    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        return 29
    return 28


def validate_time_digits(
    *,
    year: Optional[int],
    month: Optional[int],
    day: Optional[int],
    hour: int,
    minute: int,
    second: int,
    mode: str,
) -> ValidationResult:
    """Validate the digit cells the picker is editing.

    `mode` is `"time"` (year/month/day must all be None) or
    `"date_time"` (all six are required). On failure, `ok=False` and
    `error` carries a single-line operator-facing message.
    """
    if not (0 <= hour <= 23):
        return ValidationResult(ok=False, error="Invalid date — try again")
    if not (0 <= minute <= 59):
        return ValidationResult(ok=False, error="Invalid date — try again")
    if not (0 <= second <= 59):
        return ValidationResult(ok=False, error="Invalid date — try again")

    if mode == "time":
        return ValidationResult(
            ok=True,
            error=None,
            date=None,
            time=time_t(hour, minute, second),
        )

    if mode == "date_time":
        if year is None or month is None or day is None:
            return ValidationResult(ok=False, error="Invalid date — try again")
        if not (MIN_YEAR <= year <= MAX_YEAR):
            return ValidationResult(ok=False, error="Invalid date — try again")
        if not (1 <= month <= 12):
            return ValidationResult(ok=False, error="Invalid date — try again")
        max_day = _days_in_month(year, month)
        if not (1 <= day <= max_day):
            return ValidationResult(ok=False, error="Invalid date — try again")
        return ValidationResult(
            ok=True,
            error=None,
            date=date_t(year, month, day),
            time=time_t(hour, minute, second),
        )

    return ValidationResult(ok=False, error="Invalid date — try again")
