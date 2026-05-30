"""`ScheduleEvaluator` — concept decisions #4, #5, #6, #9 in code.

Pure-logic object that turns the data model (`TimelapseConfig.start` /
`.end` of type `ScheduledMoment`) into actual dispatches on
`EngineScheduler`. There is no thread; `tick()` is called inline from
the UI main loop on every iteration.

The "tick rate" is whatever the UI loop runs at (≥4 Hz idle, more on
events). Decision #9's interval check `(last_evaluated_at, trusted_now]`
is robust to UI jitter — a 3-second render stall still catches the
missed event in the following tick's interval.

The evaluator never holds `app.lock` while calling
`scheduler.cmd_*_async()`. It never uses the blocking `cmd_*` variants
— those would freeze the UI for the duration of an in-flight bracket.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    # Type-only imports to break the configs → schedule.__init__ →
    # schedule_evaluator → configs import cycle. `from __future__
    # import annotations` keeps these as strings at runtime.
    from ..configs import TimelapseConfig
    from ..engine_scheduler import EngineScheduler
    from .trusted_clock import TrustedClock

    # A crossed event: the firing wall-clock instant + the config it
    # belongs to. Defined under TYPE_CHECKING so the runtime evaluation
    # of the alias does not pull `TimelapseConfig` in mid-cycle.
    Event = tuple[datetime, TimelapseConfig]

logger = logging.getLogger(__name__)


def _select_start_winner(
    crossed: list[Event],
    *,
    configs_order: list[TimelapseConfig],
) -> Event | None:
    """Pick the winning start among events crossed in one tick.

    Decision #4: the configuration whose start time is the **most
    recent** in the crossed interval wins. Ties are broken by the
    natural order of `configs_order` (the configuration that appears
    first in the user's list wins).

    Returns `None` if `crossed` is empty.
    """
    if not crossed:
        return None
    max_ts = max(ts for ts, _ in crossed)
    contenders = [(ts, c) for ts, c in crossed if ts == max_ts]
    if len(contenders) == 1:
        return contenders[0]
    order = {c.name: i for i, c in enumerate(configs_order)}
    contenders.sort(key=lambda pair: order.get(pair[1].name, 1 << 30))
    return contenders[0]


class ScheduleEvaluator:
    """Inline schedule evaluator. One public method: `tick()`.

    Construction binds the evaluator to the live scheduler, the
    trusted clock and three provider callables that the App supplies
    as thread-safe snapshot readers.
    """

    def __init__(
        self,
        *,
        scheduler: EngineScheduler,
        trusted_clock: TrustedClock,
        configs_provider: Callable[[], list[TimelapseConfig]],
        schedule_enabled_provider: Callable[[], bool],
        active_config_name_provider: Callable[[], str | None],
        dirty_event: threading.Event | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._trusted_clock = trusted_clock
        self._configs_provider = configs_provider
        self._schedule_enabled_provider = schedule_enabled_provider
        self._active_config_name_provider = active_config_name_provider
        self._dirty_event = dirty_event
        # Frontier of the half-open interval `(last_evaluated_at, now]`.
        # `None` means "not yet seeded this armed session" — the next
        # tick will seed it without firing anything (decision #5 /
        # decision #9 seeding rule).
        self._last_evaluated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Test / debug introspection
    # ------------------------------------------------------------------
    @property
    def last_evaluated_at(self) -> datetime | None:
        return self._last_evaluated_at

    # ------------------------------------------------------------------
    # Public entry point — called from the UI main loop
    # ------------------------------------------------------------------
    def tick(self) -> None:
        # OFF resets the seeding state so a future OFF→ON does not
        # treat the gap as a window to catch up on (decision #9).
        if not self._schedule_enabled_provider():
            self._last_evaluated_at = None
            return

        trusted_now = self._trusted_clock.now()
        if trusted_now is None:
            # No baseline yet — the schedule is "armed but inert".
            # Same reset rule: keep `_last_evaluated_at` cleared so
            # that the first successful sync (which will seed the
            # baseline) is followed by a clean seeding tick rather
            # than a back-fill against stale state.
            self._last_evaluated_at = None
            return

        configs = self._configs_provider()
        active = self._active_config_name_provider()

        if self._last_evaluated_at is None:
            # First tick after boot, or after OFF→ON, or after the
            # baseline just became available. Seed the frontier and
            # fire nothing (decision #5 / decision #9 seeding rule).
            self._last_evaluated_at = trusted_now
            logger.info(
                "schedule_evaluator: seeded last_evaluated_at=%s",
                trusted_now.isoformat(),
            )
            return

        # Decision #9: gather events strictly after the previous
        # frontier and at-or-before `trusted_now`. A backward jump of
        # the trusted clock yields an empty interval — see the
        # explicit guard at the end of `tick()` for why we do NOT
        # advance the frontier in that case (concept §Decision #9
        # invariant: once seeded, `last_evaluated_at` never moves
        # backward, so no event can be re-fired).
        start_events: list[Event] = []
        end_events: list[Event] = []
        for cfg in configs:
            if cfg.start is not None:
                for ts in cfg.start.events_in(
                    self._last_evaluated_at, trusted_now
                ):
                    start_events.append((ts, cfg))
            if cfg.end is not None:
                for ts in cfg.end.events_in(
                    self._last_evaluated_at, trusted_now
                ):
                    end_events.append((ts, cfg))

        winner = _select_start_winner(start_events, configs_order=configs)

        # Decision #6: end events fire only for the configuration that
        # is currently running. End events for non-running configs are
        # ignored — they are stops for "this", not global timeline
        # events. Process ends BEFORE the start winner so the order on
        # `scheduler` is stop-then-start. In practice
        # `cmd_switch_async` implicitly stops the previous config,
        # but explicit ordering keeps the contract clean and the logs
        # readable.
        fired_end = False
        for _ts, cfg in end_events:
            if active is not None and cfg.name == active:
                self._scheduler.cmd_stop_async()
                logger.info(
                    "schedule_evaluator: END fired for %r at %s",
                    cfg.name, _ts.isoformat(),
                )
                active = None
                fired_end = True
                # Multiple end events for the same active config in
                # one tick collapse to a single stop.
                break

        fired_start = False
        if winner is not None:
            ts, cfg = winner
            if active is None:
                self._scheduler.cmd_start_async(cfg)
                logger.info(
                    "schedule_evaluator: START fired for %r at %s",
                    cfg.name, ts.isoformat(),
                )
                fired_start = True
            elif cfg.name != active:
                self._scheduler.cmd_switch_async(cfg)
                logger.info(
                    "schedule_evaluator: SWITCH fired to %r at %s "
                    "(was %r)",
                    cfg.name, ts.isoformat(), active,
                )
                fired_start = True
            # else: the winner is already the running config — no-op.

        # The frontier only moves forward in trusted-wall time. A
        # backward jump (NTP slew correction shrinking the drift, or a
        # manual override via `reset_frontier()` flow) must not pull
        # the frontier back, or the events it would un-cover would
        # re-fire on the next forward tick — clearly a bug for the
        # eclipse use case where a phase transition must happen once
        # and exactly once. Concept §Decision #9 spells this out as
        # an invariant; this `max`-style guard is the implementation.
        if trusted_now > self._last_evaluated_at:
            self._last_evaluated_at = trusted_now
        if self._dirty_event is not None and (fired_start or fired_end):
            self._dirty_event.set()

    # ------------------------------------------------------------------
    # Frontier reset — wired by the App on baseline-changing events
    # ------------------------------------------------------------------
    def reset_frontier(self) -> None:
        """Clear `last_evaluated_at` so the next `tick()` reseeds.

        Wired (in prd2.md) to App-side baseline-changing events
        (FIRST_SYNC, FORCED) so that a deliberate operator action —
        manual time entry, force-trust — resets the evaluator's
        notion of "what we already evaluated" to the new clock's now.
        Without this, a manual override that moves the trusted clock
        backward would leave the evaluator in a dead period until
        wall time catches up to the pre-override frontier (concept
        §Decision #9 + #10 interaction; PRD §5 algorithm shipped
        with this nuance deferred to the implementation note).
        """
        self._last_evaluated_at = None
        logger.info("schedule_evaluator: frontier reset")
