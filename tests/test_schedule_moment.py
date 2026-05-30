"""Tests for `ScheduledMoment` — pure data model + firing helpers."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402


class TestScheduledMomentConstruction(unittest.TestCase):
    def test_time_only_daily(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        self.assertIsNone(m.date)
        self.assertEqual(m.time, time(9, 0, 0))

    def test_date_and_time_one_shot(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        self.assertEqual(m.date, date(2026, 8, 12))
        self.assertEqual(m.time, time(11, 33, 23))

    def test_time_none_raises(self):
        with self.assertRaises((ValueError, TypeError)):
            ScheduledMoment(time=None)  # type: ignore[arg-type]


class TestNextFiringDaily(unittest.TestCase):
    """date is None → daily recurrence; always returns a datetime."""

    def test_today_when_time_not_yet_passed(self):
        m = ScheduledMoment(time=time(18, 0, 0))
        now = datetime(2026, 5, 29, 9, 0, 0)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 5, 29, 18, 0, 0))

    def test_today_when_time_equals_now(self):
        # >= now per PRD: "the next `time` >= `now`"
        m = ScheduledMoment(time=time(9, 0, 0))
        now = datetime(2026, 5, 29, 9, 0, 0)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 5, 29, 9, 0, 0))

    def test_tomorrow_when_time_already_passed_today(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        now = datetime(2026, 5, 29, 12, 0, 0)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 5, 30, 9, 0, 0))

    def test_midnight_wrap(self):
        m = ScheduledMoment(time=time(0, 0, 30))
        now = datetime(2026, 5, 29, 23, 59, 59)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 5, 30, 0, 0, 30))


class TestNextFiringOneShot(unittest.TestCase):
    """date is not None → one-shot; None if already passed."""

    def test_future_returns_combined_datetime(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        now = datetime(2026, 5, 29, 12, 0, 0)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 8, 12, 11, 33, 23))

    def test_exactly_now_returns_now(self):
        # >= now treats "exactly now" as the next firing.
        m = ScheduledMoment(time=time(12, 0, 0), date=date(2026, 5, 29))
        now = datetime(2026, 5, 29, 12, 0, 0)
        nxt = m.next_firing(now)
        self.assertEqual(nxt, datetime(2026, 5, 29, 12, 0, 0))

    def test_past_returns_none(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2020, 1, 1))
        now = datetime(2026, 5, 29, 12, 0, 0)
        self.assertIsNone(m.next_firing(now))


class TestEventsInDaily(unittest.TestCase):
    """events_in: every firing in the half-open interval (start_excl, end_incl]."""

    def test_no_event_in_empty_interval(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 29, 10, 0, 0)
        b = datetime(2026, 5, 29, 11, 0, 0)
        self.assertEqual(m.events_in(a, b), [])

    def test_one_daily_event_in_interval(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 29, 8, 0, 0)
        b = datetime(2026, 5, 29, 10, 0, 0)
        self.assertEqual(m.events_in(a, b), [datetime(2026, 5, 29, 9, 0, 0)])

    def test_daily_event_start_exclusive(self):
        # Event at exactly the start of the interval must NOT fire (exclusive).
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 29, 9, 0, 0)
        b = datetime(2026, 5, 29, 10, 0, 0)
        self.assertEqual(m.events_in(a, b), [])

    def test_daily_event_end_inclusive(self):
        # Event at exactly the end of the interval MUST fire (inclusive).
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 29, 8, 0, 0)
        b = datetime(2026, 5, 29, 9, 0, 0)
        self.assertEqual(m.events_in(a, b), [datetime(2026, 5, 29, 9, 0, 0)])

    def test_daily_multi_day_forward_jump(self):
        # 3-day forward jump → 3 daily occurrences.
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 28, 10, 0, 0)
        b = datetime(2026, 5, 31, 12, 0, 0)
        events = m.events_in(a, b)
        self.assertEqual(events, [
            datetime(2026, 5, 29, 9, 0, 0),
            datetime(2026, 5, 30, 9, 0, 0),
            datetime(2026, 5, 31, 9, 0, 0),
        ])

    def test_backward_interval_returns_empty(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        a = datetime(2026, 5, 29, 12, 0, 0)
        b = datetime(2026, 5, 29, 6, 0, 0)
        self.assertEqual(m.events_in(a, b), [])


class TestEventsInOneShot(unittest.TestCase):
    def test_one_shot_inside_interval(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 12, 11, 0, 0)
        b = datetime(2026, 8, 12, 12, 0, 0)
        self.assertEqual(m.events_in(a, b), [datetime(2026, 8, 12, 11, 33, 23)])

    def test_one_shot_outside_interval_before(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 13, 0, 0, 0)
        b = datetime(2026, 8, 14, 0, 0, 0)
        self.assertEqual(m.events_in(a, b), [])

    def test_one_shot_outside_interval_after(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 10, 0, 0, 0)
        b = datetime(2026, 8, 11, 0, 0, 0)
        self.assertEqual(m.events_in(a, b), [])

    def test_one_shot_at_exact_end_is_included(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 12, 11, 0, 0)
        b = datetime(2026, 8, 12, 11, 33, 23)
        self.assertEqual(m.events_in(a, b), [datetime(2026, 8, 12, 11, 33, 23)])

    def test_one_shot_at_exact_start_is_excluded(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 12, 11, 33, 23)
        b = datetime(2026, 8, 12, 12, 0, 0)
        self.assertEqual(m.events_in(a, b), [])

    def test_one_shot_backward_interval(self):
        m = ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12))
        a = datetime(2026, 8, 12, 12, 0, 0)
        b = datetime(2026, 8, 12, 11, 0, 0)
        self.assertEqual(m.events_in(a, b), [])


class TestScheduledMomentFrozen(unittest.TestCase):
    def test_is_frozen(self):
        m = ScheduledMoment(time=time(9, 0, 0))
        with self.assertRaises(Exception):
            m.time = time(10, 0, 0)  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
