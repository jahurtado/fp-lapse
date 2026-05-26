"""Dedicated thread that wakes the engine right when each grid mark is due.

The previous architecture polled `engine.tick()` from the UI loop at
10 Hz. That model coupled engine timing to the UI's render+blit
budget: any slow iteration (300 ms blit in the pre-numpy days, GC
pauses, log rotation, …) made `tick()` land late, push past the
tolerance window, and count the missed instant as a SKIP.

With the scheduler, the UI loop no longer ticks the engine. A
dedicated thread blocks on `Event.wait(timeout = next_mark - now)`
(kernel `nanosleep`, ±100 µs even under load) and calls `engine.tick()`
exactly when each `t0 + k·p` mark is due. The UI thread can be slow,
take long renders, or even stall briefly — none of that affects grid
precision.

Threading model:

  - The engine is mutated ONLY through this scheduler — `cmd_start()`,
    `cmd_stop()`, `cmd_switch()`, and the internal `_run()` loop that
    calls `engine.tick()`. All these acquire `_cmd_lock`, so the
    engine has effectively a single writer at any moment.
  - Engine STATUS (shots_taken, skips, state, …) is read without lock
    from the UI thread. CPython's GIL guarantees atomic reads of
    individual int/str/None fields. A reader may observe a slightly
    inconsistent snapshot (e.g. shots_taken from N, skips from
    pre-N), which is fine for display purposes.
  - `dirty_event` is set after every state-changing op so the UI loop
    knows to re-render.

The scheduler is intentionally NOT a queue-based actor: commands are
applied synchronously by the calling thread (e.g. a gpiozero button
callback) under `_cmd_lock`. If the engine is mid-shoot when a stop
command arrives, the caller blocks until the in-flight bracket
completes — matching spec §5.3 ("already-started captures are always
finished").
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .configs import TimelapseConfig
from .engine import Engine, EngineError

logger = logging.getLogger(__name__)


class EngineScheduler:
    def __init__(
        self,
        engine: Engine,
        *,
        dirty_event: Optional[threading.Event] = None,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._cmd_lock = threading.Lock()
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._dirty_event = dirty_event
        self._now = now_monotonic
        self._thread = threading.Thread(
            target=self._run, name="engine-scheduler", daemon=True,
        )

    # --- lifecycle ---
    def start(self) -> None:
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._shutdown.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def engine(self) -> Engine:
        """Read-only view of the underlying engine, for status queries.

        UI / control-server code reads `scheduler.engine.status()`,
        which reads engine fields without taking `_cmd_lock`. That's
        race-free for primitive fields because of the GIL; for
        compound state (e.g. config switch + skip reset happening
        together), reads may catch an in-between state, but that's
        acceptable for display.
        """
        return self._engine

    # --- commands ---
    def cmd_start(self, cfg: TimelapseConfig) -> None:
        with self._cmd_lock:
            try:
                self._engine.start(cfg)
            except EngineError as e:
                logger.warning("scheduler.cmd_start rejected: %s", e)
                return
        self._wake.set()
        self._notify_dirty()

    def cmd_stop(self) -> None:
        with self._cmd_lock:
            self._engine.stop()
        self._wake.set()
        self._notify_dirty()

    def cmd_switch(self, cfg: TimelapseConfig) -> None:
        with self._cmd_lock:
            try:
                self._engine.switch_to(cfg)
            except EngineError as e:
                logger.warning("scheduler.cmd_switch rejected: %s", e)
                return
        self._wake.set()
        self._notify_dirty()

    # --- internals ---
    def _notify_dirty(self) -> None:
        if self._dirty_event is not None:
            self._dirty_event.set()

    def _run(self) -> None:
        logger.info("scheduler: thread started")
        while not self._shutdown.is_set():
            with self._cmd_lock:
                next_t = self._engine.next_fire_monotonic()

            if next_t is None:
                # IDLE — sleep until something happens (start cmd or shutdown).
                self._wake.wait()
                self._wake.clear()
                continue

            wait = next_t - self._now()
            if wait > 0:
                # Sleep until the mark, but be ready to wake on a
                # state change (stop / switch / shutdown).
                if self._wake.wait(timeout=wait):
                    self._wake.clear()
                    continue

            # Time to fire. The engine still does the work of choosing
            # `k`, handling skip-counting and the actual capture.
            try:
                with self._cmd_lock:
                    self._engine.tick()
            except Exception:
                logger.exception("scheduler: engine.tick raised — continuing")

            self._notify_dirty()

        logger.info("scheduler: thread exiting")
