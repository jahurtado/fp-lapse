"""Status-bar schedule indicator — 4-state colored dot (§6, prd2.md).

The enum members describe what the operator SEES on screen (the color
of the dot), not the underlying clock state. The mapping from clock
state to color is a single function (`App._compute_schedule_indicator`).

Semantic rule:

- Color = "is the engine firing?" Green/yellow = yes; red = no.
- Yellow absorbs both "stale" (no fresh sync in ≥ 2 h) and "glitched"
  (the last NTP sync was REJECTED by the envelope) because from the
  operator's perspective the consequence is identical: the engine
  still fires, the clock has a caveat.
"""

from __future__ import annotations

from enum import Enum


class ScheduleIndicator(str, Enum):
    """4-state status-bar indicator (prd2.md §6).

    OFF    — schedule disabled; nothing rendered.
    RED    — armed, never synced this boot; engine NOT firing.
    GREEN  — armed, fresh sync (<2 h); engine firing.
    YELLOW — armed, stale OR glitched; engine firing with caveat.
    """

    OFF = "off"
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
