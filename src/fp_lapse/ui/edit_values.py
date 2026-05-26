"""Discrete value lists the edit screen cycles through with ←→.

Defined per §7.3 of docs/reference.md. Each list starts with the neutral
sentinels (`None` = "don't touch", `"auto"`) followed by camera-
specific values.

`cycle_in_list(value, values, delta)` finds `value` in `values`,
advances `delta` positions (with wrap-around), and returns the new
value. If `value` is not in the list — typical when the JSON has been
hand-edited with a non-canonical value — it snaps to the closest
numeric or, failing that, returns the first element.
"""

from __future__ import annotations

from typing import Any, List, Sequence

# Spec §7.3: discrete interval values, in seconds.
INTERVALS_S: List[float] = [
    1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 300, 600,
]

# Sigma fp shutter speeds in 1/3 EV from 1/8000 to 30 s.
SHUTTER_VALUES: List[Any] = [
    1 / 8000, 1 / 6400, 1 / 5000, 1 / 4000, 1 / 3200, 1 / 2500,
    1 / 2000, 1 / 1600, 1 / 1250, 1 / 1000, 1 / 800, 1 / 640,
    1 / 500, 1 / 400, 1 / 320, 1 / 250, 1 / 200, 1 / 160,
    1 / 125, 1 / 100, 1 / 80, 1 / 60, 1 / 50, 1 / 40,
    1 / 30, 1 / 25, 1 / 20, 1 / 15, 1 / 13, 1 / 10,
    1 / 8, 1 / 6, 1 / 5, 1 / 4,
    0.3, 0.4, 0.5, 0.6, 0.8,
    1.0, 1.3, 1.6, 2.0, 2.5, 3.2, 4.0, 5.0, 6.0, 8.0,
    10.0, 13.0, 15.0, 20.0, 25.0, 30.0,
]

# ISO values exposed by the UI cycling: full native stops only
# (`{100, 200, 400, …, 25600}`). The fp supports 1/3 EV intermediates
# and extended ranges (6..50 below, 51200..102400 above), but as a UX
# decision only the round values are exposed. The JSON validator
# (`ISO_MIN..ISO_MAX` in `configs.py`) is the hard boundary, so a
# manual editor of the JSON can still use intermediate values.
ISO_VALUES: List[Any] = [
    100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600,
]

# Standard apertures f/1.4 to f/22 in 1/3 stops. `None` means "manual
# lens with no electronic aperture control" — first slot so cycling
# from f/22 wraps to it and back to the wide end.
APERTURE_VALUES: List[Any] = [
    None,
    1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8, 3.2, 3.5, 4.0,
    4.5, 5.0, 5.6, 6.3, 7.1, 8.0, 9.0, 10.0, 11.0, 13.0,
    14.0, 16.0, 18.0, 20.0, 22.0,
]

# The Shots field cycles `1 (auto)` (= empty shots, ProgramAuto) then
# 1..MAX_SHOTS_PER_BRACKET (manual). The "AUTO" sentinel below is the
# internal value; the display name lives in the edit screen.
SHOTS_AUTO = "auto"
SHOTS_VALUES: List[Any] = [SHOTS_AUTO, 1, 2, 3, 4, 5, 6, 7, 8, 9]


def format_shots(value: Any) -> str:
    """How a `SHOTS_VALUES` entry shows up in the edit screen."""
    if value == SHOTS_AUTO:
        return "1 (auto)"
    return str(value)


def cycle_in_list(value: Any, values: Sequence[Any], delta: int) -> Any:
    """Advance `value` `delta` steps through `values` with wrap-around.

    If `value` is not in the list — typical when the JSON has been
    hand-edited with a non-canonical value — snap to the closest
    numeric (by `abs(v - value)`) or, if there are no numeric values,
    to the first element.
    """
    if not values:
        raise ValueError("values must not be empty")
    try:
        idx = values.index(value)
    except ValueError:
        idx = _snap_index(value, values)
    new_idx = (idx + delta) % len(values)
    return values[new_idx]


def _snap_index(value: Any, values: Sequence[Any]) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_idxs = [
            i for i, v in enumerate(values)
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if numeric_idxs:
            return min(numeric_idxs, key=lambda i: abs(values[i] - value))
    return 0
