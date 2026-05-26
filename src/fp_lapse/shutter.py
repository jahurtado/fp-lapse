"""Shutter value parsing and formatting.

JSON accepts these forms for `shutter` (see §3.2 of docs/reference.md):

  - "1/N"             → fraction with numerator 1 (`1/500`, `1/8000`…)
  - "0.5", "2", "30"  → decimal/integer string (seconds)
  - 0.5, 2, 30        → numeric (seconds)

`parse_shutter` normalises all forms to `float` (seconds).
`format_shutter` does the reverse for the UI following §3.2:

  - exact `1/N` with N integer → `1/N`
  - value ≥ 1 s                → `N s` (integer) or `N.NN s`
  - rest                       → `0.NNN s`

The accepted range (Sigma fp: 1/8000..30 s) is checked by `in_range`.

Auto-exposure used to be encoded as `shutter="auto"` per parameter, but
the data model now treats auto as a *config-level* property
(`TimelapseConfig.shots == ()` means "1 shot per interval with the
camera metering everything"). Individual shots therefore always carry
a numeric shutter.
"""

from __future__ import annotations

ShutterValue = float

SHUTTER_MIN_S: float = 1.0 / 8000
SHUTTER_MAX_S: float = 30.0


class ShutterValueError(ValueError):
    """The value cannot be parsed as a shutter."""


def parse_shutter(raw) -> ShutterValue:
    """Normalise a shutter value to `float` (seconds)."""
    if raw is None:
        raise ShutterValueError("shutter must be set (no per-parameter auto)")
    if isinstance(raw, bool):
        raise ShutterValueError("unsupported shutter type: bool")
    if isinstance(raw, str):
        s = raw.strip()
        if "/" in s:
            num, den = s.split("/", 1)
            if num.strip() != "1":
                raise ShutterValueError(
                    f"shutter fractions must have numerator 1, got {raw!r}"
                )
            try:
                d = float(den.strip())
            except ValueError:
                raise ShutterValueError(
                    f"invalid shutter denominator: {raw!r}"
                ) from None
            if d <= 0:
                raise ShutterValueError(
                    f"shutter denominator must be > 0: {raw!r}"
                )
            return 1.0 / d
        try:
            v = float(s)
        except ValueError:
            raise ShutterValueError(f"invalid shutter string: {raw!r}") from None
        if v <= 0:
            raise ShutterValueError(f"shutter must be > 0: {raw!r}")
        return v
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v <= 0:
            raise ShutterValueError(f"shutter must be > 0: {raw!r}")
        return v
    raise ShutterValueError(f"unsupported shutter type: {type(raw).__name__}")


def in_range(value: float) -> bool:
    """True if `value` (seconds) is within the fp's range."""
    return SHUTTER_MIN_S - 1e-9 <= value <= SHUTTER_MAX_S + 1e-9


def format_shutter(value: ShutterValue) -> str:
    """Return the on-screen representation of the shutter per §3.2."""
    v = float(value)
    if v >= 1.0:
        if v == int(v):
            return f"{int(v)} s"
        return f"{v:g} s"
    inv = 1.0 / v
    n = round(inv)
    if n > 0 and abs(inv - n) < 1e-6:
        return f"1/{n}"
    return f"{v:.3f} s"
