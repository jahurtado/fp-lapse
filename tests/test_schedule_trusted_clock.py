"""Tests for `TrustedClock` — pure logic, all Mac-testable."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.schedule.trusted_clock import SyncOutcome, TrustedClock  # noqa: E402


class FakeMonotonic:
    """Injectable monotonic clock for tests — manually advanced."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TestTrustedClockBaseline(unittest.TestCase):
    def test_initial_state(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        self.assertFalse(tc.has_baseline)
        self.assertIsNone(tc.now())
        self.assertFalse(tc.is_glitched)
        # No baseline → tolerance is the base.
        self.assertEqual(tc.tolerance_s(), TrustedClock.BASE_TOLERANCE_S)

    def test_first_sync_is_unconditionally_trusted(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        outcome = tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        self.assertEqual(outcome, SyncOutcome.FIRST_SYNC)
        self.assertTrue(tc.has_baseline)
        self.assertFalse(tc.is_glitched)

    def test_now_returns_baseline_plus_monotonic_delta(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(45.5)
        now = tc.now()
        self.assertIsNotNone(now)
        # 45.5 s past 12:00:00 → 12:00:45.500000
        self.assertEqual(now, datetime(2026, 5, 29, 12, 0, 45, 500000))


class TestTrustedClockEnvelope(unittest.TestCase):
    def test_small_correction_accepted(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # 10 s later, OS reports a wall time 100 ms ahead of prediction
        # (50 ms drift, well inside 5 s envelope).
        mono.advance(10.0)
        # Predicted: 12:00:10.0; OS reports: 12:00:10.100
        outcome = tc.on_sync_observed(
            datetime(2026, 5, 29, 12, 0, 10, 100_000)
        )
        self.assertEqual(outcome, SyncOutcome.ACCEPTED)
        self.assertFalse(tc.is_glitched)
        # Baseline advanced to absorb the fine-tuning.
        # now() == 12:00:10.100 + (0 extra monotonic)
        self.assertEqual(tc.now(), datetime(2026, 5, 29, 12, 0, 10, 100_000))

    def test_large_jump_rejected(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(60.0)
        # 4-hour jump — way outside envelope.
        outcome = tc.on_sync_observed(datetime(2026, 5, 29, 16, 1, 0))
        self.assertEqual(outcome, SyncOutcome.REJECTED)
        self.assertTrue(tc.is_glitched)
        # Baseline held: now() reflects baseline + 60 s.
        self.assertEqual(tc.now(), datetime(2026, 5, 29, 12, 1, 0))

    def test_glitch_cleared_on_subsequent_accepted(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(60.0)
        # Reject a 4-hour jump.
        tc.on_sync_observed(datetime(2026, 5, 29, 16, 1, 0))
        self.assertTrue(tc.is_glitched)
        # Now a tiny correction (50 ms) — should clear the glitch.
        mono.advance(5.0)
        # Predicted: 12:01:05; OS reports 12:01:05.050
        outcome = tc.on_sync_observed(
            datetime(2026, 5, 29, 12, 1, 5, 50_000)
        )
        self.assertEqual(outcome, SyncOutcome.ACCEPTED)
        self.assertFalse(tc.is_glitched)


class TestTrustedClockForce(unittest.TestCase):
    def test_force_trust_next_sync_then_outside_envelope_forced(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(60.0)
        # Without force, this would reject.
        tc.force_trust_next_sync()
        outcome = tc.on_sync_observed(datetime(2026, 5, 29, 16, 1, 0))
        self.assertEqual(outcome, SyncOutcome.FORCED)
        self.assertFalse(tc.is_glitched)
        # Baseline jumped to the new wall.
        self.assertEqual(tc.now(), datetime(2026, 5, 29, 16, 1, 0))

    def test_force_flag_is_one_shot(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(60.0)
        tc.force_trust_next_sync()
        # First sync after force: FORCED.
        tc.on_sync_observed(datetime(2026, 5, 29, 16, 1, 0))
        # Second sync: back to envelope checking. 4-hour jump should be REJECTED.
        mono.advance(60.0)
        # Predicted (from new baseline 16:01 + 60 s) ~ 16:02:00.
        # OS reports far away → REJECTED.
        outcome = tc.on_sync_observed(datetime(2026, 5, 29, 20, 1, 0))
        self.assertEqual(outcome, SyncOutcome.REJECTED)

    def test_force_clears_existing_glitch(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        mono.advance(60.0)
        tc.on_sync_observed(datetime(2026, 5, 29, 16, 1, 0))  # REJECTED
        self.assertTrue(tc.is_glitched)
        tc.force_trust_next_sync()
        tc.on_sync_observed(datetime(2026, 5, 29, 16, 5, 0))
        self.assertFalse(tc.is_glitched)


class TestTrustedClockTolerance(unittest.TestCase):
    def test_tolerance_floor_at_short_age(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # Age 0 → BASE_TOLERANCE_S (5 s).
        self.assertEqual(tc.tolerance_s(), 5.0)
        # Age 1 h = 3600 s → max(5, 0.0001 * 3600 + 1) = max(5, 1.36) = 5.
        mono.advance(3600.0)
        self.assertEqual(tc.tolerance_s(), 5.0)

    def test_tolerance_grows_with_age(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # Age 100_000 s → max(5, 0.0001 * 100_000 + 1) = max(5, 11) = 11.
        mono.advance(100_000.0)
        self.assertAlmostEqual(tc.tolerance_s(), 11.0, places=6)

    def test_50ms_jitter_accepted_at_any_age(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # 1 day later, 50 ms ahead of prediction.
        mono.advance(86_400.0)
        wall = datetime(2026, 5, 29, 12, 0, 0) + timedelta(seconds=86_400.05)
        outcome = tc.on_sync_observed(wall)
        self.assertEqual(outcome, SyncOutcome.ACCEPTED)

    def test_4h_jump_rejected_at_any_age(self):
        mono = FakeMonotonic(100.0)
        tc = TrustedClock(now_monotonic=mono)
        tc.on_sync_observed(datetime(2026, 5, 29, 12, 0, 0))
        # 1 day later, plus 4 hours of skew.
        mono.advance(86_400.0)
        wall = datetime(2026, 5, 29, 12, 0, 0) + timedelta(seconds=86_400.0 + 4 * 3600)
        outcome = tc.on_sync_observed(wall)
        self.assertEqual(outcome, SyncOutcome.REJECTED)


class TestSyncOutcomeEnum(unittest.TestCase):
    def test_has_four_members(self):
        self.assertEqual(
            {SyncOutcome.FIRST_SYNC, SyncOutcome.ACCEPTED,
             SyncOutcome.REJECTED, SyncOutcome.FORCED},
            set(SyncOutcome),
        )

    def test_string_values(self):
        # str-enum: values should be the canonical strings used in logs.
        self.assertEqual(SyncOutcome.FIRST_SYNC.value, "first_sync")
        self.assertEqual(SyncOutcome.ACCEPTED.value, "accepted")
        self.assertEqual(SyncOutcome.REJECTED.value, "rejected")
        self.assertEqual(SyncOutcome.FORCED.value, "forced")


if __name__ == "__main__":
    unittest.main()
