"""Timelapse execution engine — IDLE / RUNNING state machine.

Design (see §4–§6 of docs/reference.md):

- Single-thread. Each `tick()` is invoked from the main loop at ~10 Hz.
- Monotonic clock (`time.monotonic()`), injectable for deterministic
  tests.
- `t0` is set on the first `start()` and preserved across each
  `switch_to()` (hot config change). It is lost on `stop()` or on a
  new `start()`.
- The trigger grid is `t0 + k·p_current`. The next `k` to fire is the
  smallest one satisfying two conditions: strictly later than the last
  fired in the current grid, and `t0 + k·p ≥ now`.
- A bracket runs in full inside a single `tick()`; it blocks the main
  loop while it does (PTP allows no clean cancellation — §4.4).
  Subsequent `tick()`s pick up the resulting SKIPS.
- Config switch: applies on the next `tick()`. If it happens during a
  bracket the change materialises when control returns to the main
  loop, i.e. after `tick()` finishes — equivalent to §4.5.
- SKIPS counter accumulates while `t0` stays alive, resets on the next
  `start()`. Skips are counted only within the same grid; a
  `switch_to()` resets the baseline (`_next_expected_k = None`)
  without touching the running total.
- Camera errors (§6.1): log + short buzzer beep + the engine keeps
  going. The UI decides when to surface the `CAMERA NOT RESPONDING`
  banner by reading `consecutive_failures`.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Protocol

from .camera import Camera, CameraError
from .camera.iface import ExposureMode
from .configs import Shot, TimelapseConfig

logger = logging.getLogger(__name__)

ERROR_BUZZER_S: float = 0.15  # §6.1: "short beep ~150 ms"

# How much loop delay (≈1 tick at 10 Hz) we tolerate before considering
# a grid instant "lost". If we pass t0+k·p by less than
# TICK_TOLERANCE_S, we still fire that k. Beyond that, it counts as a
# skip.
TICK_TOLERANCE_S: float = 0.15


class EngineState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"


class EngineError(Exception):
    """API misuse (e.g. `start()` while RUNNING). Bug, not an operational condition."""


class BuzzerLike(Protocol):
    def beep(self, duration_s: float) -> None: ...


@dataclass(frozen=True)
class EngineStatus:
    state: EngineState
    active_config_name: Optional[str]
    shots_taken: int
    skips: int
    consecutive_failures: int
    seconds_to_next_shot: Optional[float]


class Engine:
    def __init__(
        self,
        camera: Camera,
        *,
        buzzer: Optional[BuzzerLike] = None,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._camera = camera
        self._buzzer = buzzer
        self._now = now_monotonic
        self._state: EngineState = EngineState.IDLE
        self._t0: Optional[float] = None
        self._active_config: Optional[TimelapseConfig] = None
        self._shots_taken: int = 0
        self._skips: int = 0
        self._consecutive_failures: int = 0
        # The next k we expect to fire in the CURRENT grid. `None` at
        # init or after a `switch_to()` — no baseline from which to
        # count skips (the first fire on a new grid doesn't count as a
        # skip).
        self._next_expected_k: Optional[int] = None
        # Distinguishes "first fire after start" (where `tick()` fires
        # k=0 immediately regardless of catch-up) from "first fire
        # after switch_to" (catch-up on the new grid).
        self._first_fire_done: bool = False

    # --- queries ---
    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def active_config(self) -> Optional[TimelapseConfig]:
        return self._active_config

    @property
    def shots_taken(self) -> int:
        return self._shots_taken

    @property
    def skips(self) -> int:
        return self._skips

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def t0(self) -> Optional[float]:
        return self._t0

    def seconds_to_next_shot(self) -> Optional[float]:
        if self._state != EngineState.RUNNING:
            return None
        now = self._now()
        actual_k = self._compute_target_k(now)
        target_t = self._t0 + actual_k * self._active_config.interval_s  # type: ignore[operator]
        return max(0.0, target_t - now)

    def next_fire_monotonic(self) -> Optional[float]:
        """Earliest monotonic time at which the engine wants to fire next.

        - `None` if IDLE.
        - The current `self._now()` if RUNNING and the first fire after
          `start()` hasn't happened yet (k=0 fires ASAP).
        - Otherwise `t0 + _next_expected_k * p`. If that's in the past,
          the engine wants to fire **now** — the scheduler should not
          sleep, just call `tick()` immediately and let the engine
          handle catch-up (SKIPs included).

        Used by `EngineScheduler` to pick the next wake-up. The engine
        itself does not change behavior; `tick()` is still the only
        method that fires shots and updates the grid.
        """
        if self._state != EngineState.RUNNING:
            return None
        if not self._first_fire_done:
            return self._now()
        cfg = self._active_config
        assert cfg is not None and self._t0 is not None
        next_k = self._next_expected_k if self._next_expected_k is not None else 0
        return self._t0 + next_k * cfg.interval_s

    def status(self) -> EngineStatus:
        return EngineStatus(
            state=self._state,
            active_config_name=(
                self._active_config.name if self._active_config else None
            ),
            shots_taken=self._shots_taken,
            skips=self._skips,
            consecutive_failures=self._consecutive_failures,
            seconds_to_next_shot=self.seconds_to_next_shot(),
        )

    # --- commands ---
    def start(self, config: TimelapseConfig) -> None:
        if self._state != EngineState.IDLE:
            raise EngineError("start() called while not IDLE; call stop() first")
        self._t0 = self._now()
        self._active_config = config
        self._shots_taken = 0
        self._skips = 0
        self._consecutive_failures = 0
        self._next_expected_k = None
        self._first_fire_done = False
        self._state = EngineState.RUNNING
        logger.info(
            "engine: START config=%r t0=%.3f p=%.3fs",
            config.name, self._t0, config.interval_s,
        )

    def switch_to(self, config: TimelapseConfig) -> None:
        if self._state != EngineState.RUNNING:
            raise EngineError("switch_to() called while not RUNNING")
        if self._active_config is not None and config == self._active_config:
            return
        old_name = self._active_config.name if self._active_config else "?"
        self._active_config = config
        self._next_expected_k = None  # new grid → reset baseline (not the running total)
        logger.info(
            "engine: SWITCH %s -> %s p=%.3fs",
            old_name, config.name, config.interval_s,
        )

    def stop(self) -> None:
        if self._state == EngineState.IDLE:
            return
        logger.info(
            "engine: STOP (shots=%d skips=%d)",
            self._shots_taken, self._skips,
        )
        self._state = EngineState.IDLE
        self._t0 = None
        self._active_config = None
        self._next_expected_k = None
        self._first_fire_done = False
        self._shots_taken = 0
        self._skips = 0
        self._consecutive_failures = 0

    def tick(self) -> None:
        if self._state != EngineState.RUNNING:
            return
        cfg = self._active_config
        assert cfg is not None and self._t0 is not None
        now = self._now()
        elapsed = now - self._t0
        actual_k = self._compute_target_k(now)
        target_t = self._t0 + actual_k * cfg.interval_s
        lateness = now - target_t
        # Telemetry: every tick under RUNNING logs its decision, so the
        # log captures the loop cadence and how close each tick lands
        # to the grid. Cheap (one line per tick).
        logger.debug(
            "engine: tick elapsed=%.3f k_expect=%s k_actual=%d "
            "target_t=%.3f now=%.3f lateness=%+.3f",
            elapsed,
            self._next_expected_k,
            actual_k,
            target_t,
            now,
            lateness,
        )
        # Eager skip accounting: if the k about to fire is past the
        # baseline (_next_expected_k), the intermediate instants count
        # as skips even if we haven't fired this `actual_k`'s bracket
        # yet. Keeps the counter live for the UI as soon as instants
        # are lost (§5.2).
        if self._next_expected_k is not None and actual_k > self._next_expected_k:
            skipped = actual_k - self._next_expected_k
            self._skips += skipped
            logger.warning(
                "engine: SKIP +%d (k=%d, prev=%d, total=%d, elapsed=%.3f, "
                "tgt_for_prev=%.3f, now=%.3f, over=%+.3f)",
                skipped, actual_k, self._next_expected_k, self._skips,
                elapsed,
                self._next_expected_k * cfg.interval_s,
                elapsed,
                elapsed - self._next_expected_k * cfg.interval_s,
            )
        # The next expected k is the one we just identified (not yet
        # fired). When we do fire, _fire_bracket advances it to k+1.
        self._next_expected_k = actual_k
        if now < target_t:
            return  # not time yet; skips already accounted for
        self._fire_bracket(cfg, actual_k, lateness=lateness)

    # --- internals ---
    def _compute_target_k(self, now: float) -> int:
        """k of the next fire on the active grid, given `now`.

        Three regimes:
          - First fire ever after `start()`: target k=0 (fires as soon
            as possible). If the loop is late, the fire happens with
            delay but does NOT generate a skip — k=0 is always
            honoured.
          - First fire after `switch_to()` (hot grid change): the
            smallest k such that `t0+k·p_new ≥ now` (§4.2). Also does
            not count as a skip (baseline change).
          - Normal regime: max(`_next_expected_k`, catch-up).
        """
        assert self._t0 is not None and self._active_config is not None
        p = self._active_config.interval_s
        if not self._first_fire_done:
            return 0
        # Tolerance: convert the time threshold (TICK_TOLERANCE_S) to
        # the dimensionless k-space so `ceil` doesn't penalise delays
        # smaller than one tick. Without this, `ceil` jumps to k+1 with
        # just a millisecond of jitter.
        tol_k = TICK_TOLERANCE_S / p
        catch_up_k = max(0, math.ceil((now - self._t0) / p - tol_k))
        if self._next_expected_k is None:
            return catch_up_k
        return max(self._next_expected_k, catch_up_k)

    def _fire_bracket(
        self, cfg: TimelapseConfig, k: int, *, lateness: float = 0.0
    ) -> None:
        t_start = self._now()
        shots_before = self._shots_taken
        n_shots = cfg.shots_per_interval
        logger.info(
            "engine: TICK k=%d shots=%d%s lateness=%+.3fs",
            k, n_shots, " auto" if cfg.is_auto else "", lateness,
        )
        if cfg.is_auto:
            # One shot per interval, camera meters exposure
            # (ProgramAuto). We re-set the exposure mode on every fire
            # so movement of the physical dial mid-session doesn't
            # silently break the program.
            self._fire_shot(shot=None, k=k, shot_index=1)
        else:
            for i, shot in enumerate(cfg.shots, start=1):
                self._fire_shot(shot, k=k, shot_index=i)
        duration = self._now() - t_start
        shots_in_bracket = self._shots_taken - shots_before
        # Grid-instant accounting: a bracket where ZERO shots succeeded
        # (typical when the camera lost its USB link mid-session) still
        # consumed its grid instant and produced no images — from the
        # user's perspective that's a lost instant, same as if the
        # previous bracket had run long. Count it as a skip so the UI's
        # SKIPS counter shows the problem.
        if shots_in_bracket == 0:
            self._skips += 1
            logger.warning(
                "engine: BRACKET FAILED k=%d (0/%d shots) → SKIP +1 (total=%d)",
                k, n_shots, self._skips,
            )
        logger.info(
            "engine: BRACKET DONE k=%d shots=%d/%d duration=%.3fs",
            k, shots_in_bracket, n_shots, duration,
        )
        self._next_expected_k = k + 1
        self._first_fire_done = True

    def _fire_shot(
        self, shot: Optional[Shot], *, k: int, shot_index: int
    ) -> None:
        t_start = self._now()
        try:
            self._apply_shot_params(shot)
            self._camera.shoot()
            self._shots_taken += 1
            self._consecutive_failures = 0
            logger.info(
                "engine: shot ok (k=%d, shot=%d, dur=%.3fs)",
                k, shot_index, self._now() - t_start,
            )
        except CameraError as e:
            self._consecutive_failures += 1
            logger.error(
                "engine: shot failed (k=%d, shot=%d, cause=%s:%s, consec=%d)",
                k, shot_index, type(e).__name__, e, self._consecutive_failures,
            )
            if self._buzzer is not None:
                try:
                    self._buzzer.beep(ERROR_BUZZER_S)
                except Exception:
                    logger.exception("engine: buzzer.beep failed")

    def _apply_shot_params(self, shot: Optional[Shot]) -> None:
        """Set the camera up for the next shoot.

        We always force `exposure_mode` (Manual or Program) before each
        fire — that way moving the physical dial mid-session is
        recovered automatically on the next shot. The cost is one extra
        PTP roundtrip but it's negligible vs. the actual capture.
        """
        if shot is None:
            # Auto mode: camera meters everything.
            self._camera.set_params(exposure_mode=ExposureMode.PROGRAM)
            return
        self._camera.set_params(
            shutter_s=shot.shutter,
            iso=shot.iso,
            aperture=shot.aperture,
            exposure_mode=ExposureMode.MANUAL,
        )
