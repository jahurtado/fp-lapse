"""Tests for `TimeSyncProber` — the inline-driven NTP poller."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.schedule.time_sync_prober import TimeSyncProber  # noqa: E402


def _runner_yes(time_usec: str = "Wed 2026-05-29 14:32:07 UTC"):
    """Build a fake timedatectl runner returning NTPSynchronized=yes."""
    def runner(cmd):
        return f"yes\n{time_usec}\n"
    return runner


def _runner_no(time_usec: str = "Wed 2026-05-29 14:32:07 UTC"):
    def runner(cmd):
        return f"no\n{time_usec}\n"
    return runner


class FakeMonotonic:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeWall:
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


class TestNotSyncedYet(unittest.TestCase):
    def test_no_then_no_does_not_call_on_sync(self):
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        on_sync = MagicMock()
        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=_runner_no(),
            now_wall=wall,
            now_monotonic=mono,
        )
        prober.maybe_poll()
        self.assertFalse(prober.is_synced())
        self.assertIsNone(prober.last_successful_sync_at_monotonic())
        on_sync.assert_not_called()


class TestFirstSync(unittest.TestCase):
    def test_no_then_yes_fires_on_sync_once(self):
        mono = FakeMonotonic(10.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        on_sync = MagicMock()
        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=_runner_yes(),
            now_wall=wall,
            now_monotonic=mono,
        )
        prober.maybe_poll()
        self.assertTrue(prober.is_synced())
        self.assertEqual(prober.last_successful_sync_at_monotonic(), 10.0)
        self.assertEqual(
            prober.last_successful_sync_wall(),
            datetime(2026, 5, 29, 14, 32, 7),
        )
        on_sync.assert_called_once_with(datetime(2026, 5, 29, 14, 32, 7))


class TestRepeatedSync(unittest.TestCase):
    def test_yes_with_same_time_usec_does_not_refire(self):
        mono = FakeMonotonic(10.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        on_sync = MagicMock()
        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=_runner_yes("Wed 2026-05-29 14:32:07 UTC"),
            now_wall=wall,
            now_monotonic=mono,
        )
        prober.maybe_poll()
        self.assertEqual(on_sync.call_count, 1)
        # Advance past the gate; same TimeUSec → no refire.
        mono.advance(120.0)
        prober.maybe_poll()
        self.assertEqual(on_sync.call_count, 1)

    def test_yes_with_changed_time_usec_refires(self):
        mono = FakeMonotonic(10.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        on_sync = MagicMock()

        time_usec_box = ["Wed 2026-05-29 14:32:07 UTC"]

        def runner(cmd):
            return f"yes\n{time_usec_box[0]}\n"

        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=runner,
            now_wall=wall,
            now_monotonic=mono,
        )
        prober.maybe_poll()
        self.assertEqual(on_sync.call_count, 1)
        # Advance, change TimeUSec, advance wall too.
        mono.advance(120.0)
        wall.dt = datetime(2026, 5, 29, 14, 34, 9)
        time_usec_box[0] = "Wed 2026-05-29 14:34:09 UTC"
        prober.maybe_poll()
        self.assertEqual(on_sync.call_count, 2)
        on_sync.assert_called_with(datetime(2026, 5, 29, 14, 34, 9))

    def test_yes_then_no_does_not_clear_is_synced(self):
        """Decision #2 invariant: once observed as synced, the prober
        never un-syncs even if `timedatectl` later reports `no`. A
        transient `no` (rare, but possible on a flaky NTP server or a
        brief offline window) must not flip `is_synced()` back to False
        — the schedule layer treats sync as a one-way latch."""
        mono = FakeMonotonic(10.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        on_sync = MagicMock()
        reply_box = ["yes\nWed 2026-05-29 14:32:07 UTC\n"]

        def runner(cmd):
            return reply_box[0]

        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=runner,
            now_wall=wall,
            now_monotonic=mono,
            poll_interval_s=60.0,
        )
        prober.maybe_poll()                          # first poll → yes
        self.assertTrue(prober.is_synced())
        self.assertEqual(on_sync.call_count, 1)
        # Past the gate; switch to a `no` reply.
        mono.advance(120.0)
        reply_box[0] = "no\nWed 2026-05-29 14:34:09 UTC\n"
        prober.maybe_poll()                          # second poll → no
        # The latch holds: still synced, no extra on_sync fired.
        self.assertTrue(prober.is_synced())
        self.assertEqual(on_sync.call_count, 1)


class TestGate(unittest.TestCase):
    def test_back_to_back_polls_only_run_runner_once(self):
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        calls = {"n": 0}

        def runner(cmd):
            calls["n"] += 1
            return "yes\nWed 2026-05-29 14:32:07 UTC\n"

        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=runner,
            now_wall=wall,
            now_monotonic=mono,
        )
        prober.maybe_poll()
        prober.maybe_poll()
        prober.maybe_poll()
        self.assertEqual(calls["n"], 1)

    def test_gate_releases_after_poll_interval(self):
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        calls = {"n": 0}

        def runner(cmd):
            calls["n"] += 1
            return "no\nWed 2026-05-29 14:32:07 UTC\n"

        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=runner,
            now_wall=wall,
            now_monotonic=mono,
            poll_interval_s=60.0,
        )
        prober.maybe_poll()
        self.assertEqual(calls["n"], 1)
        # 30 s later — still gated.
        mono.advance(30.0)
        prober.maybe_poll()
        self.assertEqual(calls["n"], 1)
        # Past 60 s — runner called again.
        mono.advance(31.0)
        prober.maybe_poll()
        self.assertEqual(calls["n"], 2)


class TestForcePoll(unittest.TestCase):
    """Addendum A1: `force_poll()` bypasses the cadence gate."""

    def test_force_poll_runs_even_inside_the_gate(self):
        """Two back-to-back `maybe_poll()` calls hit the gate (one
        runner call total). A `force_poll()` between them still polls."""
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 0, 0))
        runner_calls = {"n": 0}

        def runner(cmd):
            runner_calls["n"] += 1
            return "yes\nWed 2026-05-29 14:00:00 UTC\n"

        prober = TimeSyncProber(
            on_sync=lambda w: None,
            timedatectl_runner=runner,
            force_sync_runner=lambda: None,
            now_wall=wall,
            now_monotonic=mono,
            poll_interval_s=60.0,
        )
        prober.maybe_poll()                          # gate empty → polls
        prober.maybe_poll()                          # gated → no poll
        prober.force_poll()                          # bypass → polls
        self.assertEqual(runner_calls["n"], 2)

    def test_force_poll_updates_the_gate(self):
        """After `force_poll()`, the next `maybe_poll()` is gated for
        the configured interval."""
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 0, 0))
        runner_calls = {"n": 0}

        def runner(cmd):
            runner_calls["n"] += 1
            return "yes\nWed 2026-05-29 14:00:00 UTC\n"

        prober = TimeSyncProber(
            on_sync=lambda w: None,
            timedatectl_runner=runner,
            force_sync_runner=lambda: None,
            now_wall=wall,
            now_monotonic=mono,
            poll_interval_s=60.0,
        )
        prober.force_poll()                          # poll #1
        prober.maybe_poll()                          # gated, no poll
        self.assertEqual(runner_calls["n"], 1)
        mono.advance(61.0)                           # gate releases
        prober.maybe_poll()                          # poll #2
        self.assertEqual(runner_calls["n"], 2)


class TestForceSync(unittest.TestCase):
    def test_request_force_sync_calls_runner_once_non_blocking(self):
        forcer = MagicMock()
        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=_runner_no(),
            force_sync_runner=forcer,
        )
        prober.request_force_sync()
        forcer.assert_called_once()

    def test_request_force_sync_swallows_runner_failure(self):
        forcer = MagicMock(side_effect=RuntimeError("nope"))
        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=_runner_no(),
            force_sync_runner=forcer,
        )
        # Must not raise.
        prober.request_force_sync()
        forcer.assert_called_once()


class TestThreadIdentity(unittest.TestCase):
    def test_on_sync_called_on_invoking_thread(self):
        mono = FakeMonotonic(0.0)
        wall = FakeWall(datetime(2026, 5, 29, 14, 32, 7))
        observed_thread_id = []

        def on_sync(dt):
            observed_thread_id.append(threading.get_ident())

        prober = TimeSyncProber(
            on_sync=on_sync,
            timedatectl_runner=_runner_yes(),
            now_wall=wall,
            now_monotonic=mono,
        )
        # Call from this thread.
        prober.maybe_poll()
        self.assertEqual(observed_thread_id, [threading.get_ident()])


class TestMalformedRunnerOutput(unittest.TestCase):
    def test_empty_output_is_treated_as_no_sync(self):
        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=lambda cmd: "",
            now_wall=FakeWall(datetime(2026, 5, 29, 14, 32, 7)),
            now_monotonic=FakeMonotonic(0.0),
        )
        prober.maybe_poll()
        self.assertFalse(prober.is_synced())

    def test_runner_exception_does_not_propagate(self):
        def boom(cmd):
            raise RuntimeError("command failed")
        prober = TimeSyncProber(
            on_sync=MagicMock(),
            timedatectl_runner=boom,
            now_wall=FakeWall(datetime(2026, 5, 29, 14, 32, 7)),
            now_monotonic=FakeMonotonic(0.0),
        )
        # Must not raise — the prober should keep the system running
        # even if timedatectl is unhappy.
        prober.maybe_poll()
        self.assertFalse(prober.is_synced())


if __name__ == "__main__":
    unittest.main()
