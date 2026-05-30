"""Tests for `ScheduleEvaluator` — concept decisions #4, #5, #6, #9.

The evaluator is pure synchronous logic driven from the UI loop. All
tests here drive `tick()` directly with fake providers, a fake
`TrustedClock` (returns whatever wall time the test sets) and a fake
`EngineScheduler` that records `cmd_*_async` calls in order.

No real threads, no real I/O.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.configs import Shot, TimelapseConfig  # noqa: E402
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402
from fp_lapse.schedule.schedule_evaluator import (  # noqa: E402
    ScheduleEvaluator,
    _select_start_winner,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeScheduler:
    """Records dispatch calls in order. The evaluator only ever uses
    the `_async` variants; we expose the blocking ones too so a test
    failure is obvious (any unexpected blocking call breaks the
    invariant)."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Optional[str]]] = []

    # Async — the only variants the evaluator may call.
    def cmd_start_async(self, cfg: TimelapseConfig) -> None:
        self.calls.append(("start_async", cfg.name))

    def cmd_switch_async(self, cfg: TimelapseConfig) -> None:
        self.calls.append(("switch_async", cfg.name))

    def cmd_stop_async(self) -> None:
        self.calls.append(("stop_async", None))

    # Blocking — invariant: never called by the evaluator.
    def cmd_start(self, cfg: TimelapseConfig) -> None:
        self.calls.append(("start_BLOCKING", cfg.name))

    def cmd_switch(self, cfg: TimelapseConfig) -> None:
        self.calls.append(("switch_BLOCKING", cfg.name))

    def cmd_stop(self) -> None:
        self.calls.append(("stop_BLOCKING", None))


class FakeTrustedClock:
    """Returns whatever wall time the test pokes into `wall`."""

    def __init__(self) -> None:
        self.wall: Optional[datetime] = None

    def now(self) -> Optional[datetime]:
        return self.wall


# ----------------------------------------------------------------------
# Config builders
# ----------------------------------------------------------------------


def cfg(
    name: str,
    *,
    start: Optional[ScheduledMoment] = None,
    end: Optional[ScheduledMoment] = None,
) -> TimelapseConfig:
    return TimelapseConfig(
        name=name,
        interval_s=10.0,
        shots=(Shot(shutter=1 / 30, iso=200),),
        start=start,
        end=end,
    )


def make_evaluator(
    *,
    configs: List[TimelapseConfig],
    enabled: bool = True,
    active: Optional[str] = None,
    dirty_event: Optional[threading.Event] = None,
) -> Tuple[ScheduleEvaluator, FakeScheduler, FakeTrustedClock, dict]:
    """Build an evaluator with mutable provider closures.

    The returned `dict` (`state`) lets a test flip `enabled`,
    `active`, or swap `configs` between ticks.
    """
    state = {"enabled": enabled, "active": active, "configs": configs}
    scheduler = FakeScheduler()
    trusted_clock = FakeTrustedClock()
    ev = ScheduleEvaluator(
        scheduler=scheduler,
        trusted_clock=trusted_clock,
        configs_provider=lambda: list(state["configs"]),
        schedule_enabled_provider=lambda: state["enabled"],
        active_config_name_provider=lambda: state["active"],
        dirty_event=dirty_event,
    )
    return ev, scheduler, trusted_clock, state


# ----------------------------------------------------------------------
# Disabled / no-baseline gates
# ----------------------------------------------------------------------


class TestDisabledGate(unittest.TestCase):
    def test_disabled_is_noop_and_resets_frontier(self):
        ev, sch, clk, state = make_evaluator(
            configs=[cfg("A", start=ScheduledMoment(time=time(9, 0)))],
            enabled=False,
        )
        clk.wall = datetime(2026, 5, 29, 12, 0)
        ev.tick()
        self.assertEqual(sch.calls, [])
        self.assertIsNone(ev.last_evaluated_at)

    def test_disabling_after_seeding_clears_frontier(self):
        """OFF after the evaluator was seeded forgets the frontier so a
        subsequent ON re-seeds rather than back-filling the OFF gap."""
        ev, sch, clk, state = make_evaluator(
            configs=[cfg("A", start=ScheduledMoment(time=time(9, 0)))],
            enabled=True,
        )
        clk.wall = datetime(2026, 5, 29, 8, 0)
        ev.tick()
        self.assertEqual(ev.last_evaluated_at, datetime(2026, 5, 29, 8, 0))
        state["enabled"] = False
        ev.tick()
        self.assertIsNone(ev.last_evaluated_at)
        self.assertEqual(sch.calls, [])


class TestNoBaselineGate(unittest.TestCase):
    def test_no_baseline_is_noop_and_resets_frontier(self):
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("A", start=ScheduledMoment(time=time(9, 0)))],
        )
        clk.wall = None
        ev.tick()
        self.assertEqual(sch.calls, [])
        self.assertIsNone(ev.last_evaluated_at)


# ----------------------------------------------------------------------
# Seeding (decision #5 + #9)
# ----------------------------------------------------------------------


class TestFirstArmedTickSeeds(unittest.TestCase):
    def test_first_tick_seeds_frontier_and_fires_nothing(self):
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("A", start=ScheduledMoment(time=time(9, 0)))],
        )
        clk.wall = datetime(2026, 5, 29, 8, 30)
        ev.tick()
        self.assertEqual(sch.calls, [])
        self.assertEqual(ev.last_evaluated_at, datetime(2026, 5, 29, 8, 30))

    def test_off_to_on_reseeds_no_backfill(self):
        """While OFF, a scheduled instant passes. After ON, that
        instant must NOT fire — the seeding tick covers the gap."""
        target = datetime(2026, 5, 29, 9, 0)
        ev, sch, clk, state = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=target.time(), date=target.date()))
            ],
            enabled=False,
        )
        # While OFF, simulate time passing past the scheduled instant.
        clk.wall = datetime(2026, 5, 29, 10, 0)
        ev.tick()  # OFF → no-op
        # Operator arms the schedule.
        state["enabled"] = True
        clk.wall = datetime(2026, 5, 29, 10, 0, 1)
        ev.tick()  # first armed tick → seed, no fire
        self.assertEqual(sch.calls, [])
        self.assertEqual(
            ev.last_evaluated_at, datetime(2026, 5, 29, 10, 0, 1)
        )
        # Subsequent ticks also don't fire the past one-shot.
        clk.wall = datetime(2026, 5, 29, 10, 0, 2)
        ev.tick()
        self.assertEqual(sch.calls, [])


# ----------------------------------------------------------------------
# Start firing (decisions #4 + #5)
# ----------------------------------------------------------------------


class TestOneShotStart(unittest.TestCase):
    def test_one_shot_in_future_fires_when_crossed(self):
        target = datetime(2026, 8, 12, 11, 33, 23)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("Totality", start=ScheduledMoment(
                    time=target.time(), date=target.date()
                )),
            ],
        )
        # Seed before the event.
        clk.wall = datetime(2026, 8, 12, 11, 33, 22)
        ev.tick()
        self.assertEqual(sch.calls, [])
        # Cross the event.
        clk.wall = datetime(2026, 8, 12, 11, 33, 24)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "Totality")])

    def test_one_shot_in_past_at_seeding_never_fires(self):
        """Decision #5: no catch-up. A one-shot whose datetime is
        before the seeding tick must never fire from this session."""
        past = datetime(2026, 1, 1, 9, 0)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=past.time(), date=past.date())),
            ],
        )
        clk.wall = datetime(2026, 5, 29, 12, 0)
        ev.tick()  # seed at the much-later time
        clk.wall = datetime(2026, 5, 29, 12, 0, 1)
        ev.tick()
        self.assertEqual(sch.calls, [])


class TestDailyStart(unittest.TestCase):
    def test_daily_already_passed_today_does_not_fire_today(self):
        """Decision #4 + #5: daily 09:00 with seeding at 10:00 must
        NOT fire today's 09:00; tomorrow's 09:00 fires when crossed."""
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("Sunrise", start=ScheduledMoment(time=time(9, 0)))],
        )
        clk.wall = datetime(2026, 5, 29, 10, 0)
        ev.tick()  # seed
        clk.wall = datetime(2026, 5, 29, 10, 0, 1)
        ev.tick()
        self.assertEqual(sch.calls, [])
        # Advance to tomorrow morning 09:00:01.
        clk.wall = datetime(2026, 5, 30, 9, 0, 1)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "Sunrise")])

    def test_daily_today_in_future_fires_when_crossed(self):
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("Sunset", start=ScheduledMoment(time=time(18, 0)))],
        )
        clk.wall = datetime(2026, 5, 29, 17, 59, 59)
        ev.tick()  # seed
        clk.wall = datetime(2026, 5, 29, 18, 0, 1)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "Sunset")])


# ----------------------------------------------------------------------
# Overlap resolution (decision #4)
# ----------------------------------------------------------------------


class TestOverlapResolution(unittest.TestCase):
    def test_latest_passed_start_wins(self):
        """Two starts crossed in the same tick; latest wins."""
        eclipse_day = date(2026, 8, 12)
        partial = ScheduledMoment(time=time(10, 0), date=eclipse_day)
        totality = ScheduledMoment(time=time(11, 33, 23), date=eclipse_day)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("Partial-1", start=partial),
                cfg("Totality", start=totality),
            ],
        )
        # Seed at 09:00 so both will fall in the window after a jump.
        clk.wall = datetime(2026, 8, 12, 9, 0)
        ev.tick()
        # Big jump past both (e.g. NTP correction after waking up
        # from a long no-network stretch).
        clk.wall = datetime(2026, 8, 12, 12, 0)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "Totality")])

    def test_tie_on_timestamp_natural_order_wins(self):
        same = ScheduledMoment(
            time=time(11, 33, 23), date=date(2026, 8, 12)
        )
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("First", start=same), cfg("Second", start=same)],
        )
        clk.wall = datetime(2026, 8, 12, 11, 33, 22)
        ev.tick()
        clk.wall = datetime(2026, 8, 12, 11, 33, 24)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "First")])

    def test_winner_already_running_is_noop(self):
        """If the most-recent-passed-start is already the active
        config, no command is dispatched (start nor switch)."""
        target = datetime(2026, 8, 12, 11, 33, 23)
        ev, sch, clk, state = make_evaluator(
            configs=[
                cfg("Totality", start=ScheduledMoment(
                    time=target.time(), date=target.date()
                )),
            ],
            active="Totality",
        )
        clk.wall = datetime(2026, 8, 12, 11, 33, 22)
        ev.tick()
        clk.wall = datetime(2026, 8, 12, 11, 33, 24)
        ev.tick()
        self.assertEqual(sch.calls, [])

    def test_winner_switches_from_other_active(self):
        target = datetime(2026, 8, 12, 11, 33, 23)
        ev, sch, clk, state = make_evaluator(
            configs=[
                cfg("Partial-1"),
                cfg("Totality", start=ScheduledMoment(
                    time=target.time(), date=target.date()
                )),
            ],
            active="Partial-1",
        )
        clk.wall = datetime(2026, 8, 12, 11, 33, 22)
        ev.tick()
        clk.wall = datetime(2026, 8, 12, 11, 33, 24)
        ev.tick()
        self.assertEqual(sch.calls, [("switch_async", "Totality")])


# ----------------------------------------------------------------------
# End firing (decision #6)
# ----------------------------------------------------------------------


class TestEndFiring(unittest.TestCase):
    def test_end_fires_only_for_active_config(self):
        ev, sch, clk, state = make_evaluator(
            configs=[
                cfg("A", end=ScheduledMoment(time=time(17, 0))),
                cfg("B", end=ScheduledMoment(time=time(17, 0))),
            ],
            active="A",
        )
        clk.wall = datetime(2026, 5, 29, 16, 59, 59)
        ev.tick()
        clk.wall = datetime(2026, 5, 29, 17, 0, 1)
        ev.tick()
        # B is not active so its end is ignored.
        self.assertEqual(sch.calls, [("stop_async", None)])

    def test_end_for_idle_config_is_ignored(self):
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("A", end=ScheduledMoment(time=time(17, 0)))],
            active=None,
        )
        clk.wall = datetime(2026, 5, 29, 16, 59, 59)
        ev.tick()
        clk.wall = datetime(2026, 5, 29, 17, 0, 1)
        ev.tick()
        self.assertEqual(sch.calls, [])

    def test_end_then_start_in_same_tick_orders_stop_first(self):
        """When the running config's end AND another config's start
        both cross in the same tick, the engine sees stop → start."""
        eclipse_day = date(2026, 8, 12)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", end=ScheduledMoment(time=time(11, 0), date=eclipse_day)),
                cfg("B", start=ScheduledMoment(
                    time=time(11, 0, 30), date=eclipse_day
                )),
            ],
            active="A",
        )
        clk.wall = datetime(2026, 8, 12, 10, 59, 59)
        ev.tick()
        clk.wall = datetime(2026, 8, 12, 11, 1)
        ev.tick()
        self.assertEqual(
            sch.calls,
            [("stop_async", None), ("start_async", "B")],
        )


# ----------------------------------------------------------------------
# Clock jumps (decision #9)
# ----------------------------------------------------------------------


class TestForwardJump(unittest.TestCase):
    def test_forward_jump_crossing_one_start_fires_it(self):
        target = datetime(2026, 5, 29, 10, 0)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=target.time(), date=target.date())),
            ],
        )
        clk.wall = datetime(2026, 5, 29, 9, 55)
        ev.tick()  # seed
        # NTP correction jumps the trusted clock forward past 10:00.
        clk.wall = datetime(2026, 5, 29, 10, 2)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "A")])

    def test_forward_jump_crossing_zero_starts_fires_nothing(self):
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=time(15, 0))),
            ],
        )
        clk.wall = datetime(2026, 5, 29, 9, 0)
        ev.tick()  # seed
        clk.wall = datetime(2026, 5, 29, 11, 0)  # big jump, no event crossed
        ev.tick()
        self.assertEqual(sch.calls, [])


class TestBackwardJump(unittest.TestCase):
    def test_backward_jump_fires_nothing_and_does_not_advance_frontier(self):
        """Concept §Decision #9 invariant: once seeded, the frontier
        never moves backward. A backward jump leaves the interval
        empty (nothing fires) AND leaves the frontier untouched (so a
        subsequent forward tick to a still-earlier-than-old value
        does not back-fire events the evaluator already processed)."""
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=time(10, 0, 30))),
            ],
        )
        clk.wall = datetime(2026, 5, 29, 10, 1)
        ev.tick()
        seeded = ev.last_evaluated_at
        self.assertEqual(seeded, datetime(2026, 5, 29, 10, 1))
        # Backward jump (e.g. NTP fine-tuning shrinking a forward
        # drift, or an operator force-trust to a slightly earlier
        # time).
        clk.wall = datetime(2026, 5, 29, 9, 30)
        ev.tick()
        self.assertEqual(sch.calls, [])
        self.assertEqual(ev.last_evaluated_at, seeded)  # NOT advanced

    def test_reset_frontier_allows_post_override_evaluation(self):
        """After a deliberate baseline reset (manual time entry /
        FORCED sync) the App calls `reset_frontier()`; the next tick
        reseeds at the new clock and resumes evaluation cleanly."""
        target = datetime(2026, 5, 29, 9, 30, 30)
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=target.time(), date=target.date())),
            ],
        )
        clk.wall = datetime(2026, 5, 29, 10, 1)
        ev.tick()  # seed at 10:01 — the 09:30:30 one-shot is in the past
        # Operator sets the clock manually to a fresh earlier time;
        # App calls reset_frontier() on the evaluator.
        ev.reset_frontier()
        self.assertIsNone(ev.last_evaluated_at)
        # First tick post-reset reseeds at the new wall time.
        clk.wall = datetime(2026, 5, 29, 9, 30, 29)
        ev.tick()
        self.assertEqual(sch.calls, [])
        self.assertEqual(ev.last_evaluated_at, datetime(2026, 5, 29, 9, 30, 29))
        # Now crossing 09:30:30 fires the one-shot.
        clk.wall = datetime(2026, 5, 29, 9, 30, 31)
        ev.tick()
        self.assertEqual(sch.calls, [("start_async", "A")])


# ----------------------------------------------------------------------
# Dirty-event signaling
# ----------------------------------------------------------------------


class TestDirtyEvent(unittest.TestCase):
    def test_dirty_event_set_on_fire(self):
        target = datetime(2026, 5, 29, 10, 0)
        dirty = threading.Event()
        ev, sch, clk, _ = make_evaluator(
            configs=[
                cfg("A", start=ScheduledMoment(time=target.time(), date=target.date())),
            ],
            dirty_event=dirty,
        )
        clk.wall = datetime(2026, 5, 29, 9, 59)
        ev.tick()
        self.assertFalse(dirty.is_set())
        clk.wall = datetime(2026, 5, 29, 10, 0, 1)
        ev.tick()
        self.assertTrue(dirty.is_set())

    def test_dirty_event_not_set_when_nothing_fires(self):
        dirty = threading.Event()
        ev, sch, clk, _ = make_evaluator(
            configs=[cfg("A", start=ScheduledMoment(time=time(15, 0)))],
            dirty_event=dirty,
        )
        clk.wall = datetime(2026, 5, 29, 9, 0)
        ev.tick()
        clk.wall = datetime(2026, 5, 29, 9, 0, 1)
        ev.tick()
        self.assertFalse(dirty.is_set())


# ----------------------------------------------------------------------
# Invariant: evaluator never uses the blocking `cmd_*` variants
# ----------------------------------------------------------------------


class TestNeverUsesBlocking(unittest.TestCase):
    def test_no_blocking_call_in_a_typical_session(self):
        eclipse_day = date(2026, 8, 12)
        ev, sch, clk, state = make_evaluator(
            configs=[
                cfg("Partial-1", start=ScheduledMoment(
                    time=time(10, 0), date=eclipse_day,
                )),
                cfg("Totality", start=ScheduledMoment(
                    time=time(11, 33, 23), date=eclipse_day,
                ), end=ScheduledMoment(
                    time=time(11, 36, 9), date=eclipse_day,
                )),
                cfg("Partial-2", start=ScheduledMoment(
                    time=time(11, 36, 10), date=eclipse_day,
                ), end=ScheduledMoment(
                    time=time(13, 0), date=eclipse_day,
                )),
            ],
        )
        # Seed early.
        clk.wall = datetime(2026, 8, 12, 9, 59, 59)
        ev.tick()
        # Walk through the eclipse with tiny forward ticks.
        for wall in [
            datetime(2026, 8, 12, 10, 0, 1),         # Partial-1 starts
            datetime(2026, 8, 12, 11, 33, 24),       # Totality starts
            datetime(2026, 8, 12, 11, 36, 10),       # Partial-2 starts (also Totality.end)
            datetime(2026, 8, 12, 13, 0, 1),         # Partial-2.end
        ]:
            # Tell the evaluator which config is "running" so end
            # logic mirrors the engine's perspective.
            if wall >= datetime(2026, 8, 12, 13, 0, 1):
                state["active"] = "Partial-2"
            elif wall >= datetime(2026, 8, 12, 11, 36, 10):
                state["active"] = "Totality"  # before this tick processes
            elif wall >= datetime(2026, 8, 12, 11, 33, 24):
                state["active"] = "Partial-1"
            elif wall >= datetime(2026, 8, 12, 10, 0, 1):
                state["active"] = None  # first start tick → from nothing
            clk.wall = wall
            ev.tick()
        # No blocking calls anywhere — only async variants.
        for kind, _ in sch.calls:
            self.assertFalse(
                "BLOCKING" in kind,
                f"evaluator used a blocking call: {kind}",
            )


# ----------------------------------------------------------------------
# Pure helper: `_select_start_winner`
# ----------------------------------------------------------------------


class TestSelectStartWinner(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(_select_start_winner([], configs_order=[]))

    def test_single_returns_it(self):
        c = cfg("A")
        ts = datetime(2026, 5, 29, 10, 0)
        self.assertEqual(
            _select_start_winner([(ts, c)], configs_order=[c]),
            (ts, c),
        )

    def test_latest_timestamp_wins(self):
        a, b = cfg("A"), cfg("B")
        ta = datetime(2026, 5, 29, 10, 0)
        tb = datetime(2026, 5, 29, 11, 0)
        out = _select_start_winner([(ta, a), (tb, b)], configs_order=[a, b])
        self.assertEqual(out, (tb, b))

    def test_tie_breaks_by_natural_order(self):
        a, b = cfg("A"), cfg("B")
        ts = datetime(2026, 5, 29, 10, 0)
        # B passed first to crossed-list, but A appears first in configs_order.
        out = _select_start_winner(
            [(ts, b), (ts, a)], configs_order=[a, b],
        )
        self.assertEqual(out, (ts, a))


if __name__ == "__main__":
    unittest.main()
