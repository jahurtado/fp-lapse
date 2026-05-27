"""Pure value translation for the Nikon D5600 gphoto2 adapter.

gphoto2 exposes camera settings as *choice strings* (e.g. shutter speed as
``"0.0020s"``, ISO as ``"800"``, aperture as ``"f/5.6"``). The `Camera`
Protocol, by contrast, speaks human-friendly numbers: shutter in seconds,
ISO as an int, aperture as an f-number. These helpers bridge the two — and
they do it **without importing `gphoto2`**, operating only on the list of
choice strings the caller supplies.

Keeping them pure (no C binding, no live camera) means the whole
nearest-match logic — the part most likely to have an off-by-one bug — is
unit-testable on a vanilla Mac. The adapter (`nikon_gphoto.py`, which *does*
import `gphoto2`) imports these helpers and feeds them the widget's choice
list at runtime.

Each ``*_to_label`` function returns the nearest valid label (or ``None`` if
the choice list has no numeric entries at all), clamping to the extremes when
the request is out of range. Each ``label_to_*`` is the inverse parse,
returning ``None`` for non-numeric labels such as ``"Bulb"``, ``"Time"`` or
``"Auto"``.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

# Labels that are valid gphoto2 choices but carry no numeric exposure value;
# excluded as nearest-match targets and parsed to None.
_NON_NUMERIC_SHUTTER = {"bulb", "time"}
_NON_NUMERIC_ISO = {"auto"}


def _shutter_label_value(label: str) -> Optional[float]:
    """Parse a `shutterspeed` label like ``"0.0020s"`` → 0.002 seconds.

    Returns ``None`` for non-numeric labels (``"Bulb"``, ``"Time"``).
    """
    s = label.strip()
    if s.lower() in _NON_NUMERIC_SHUTTER:
        return None
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)s?", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _iso_label_value(label: str) -> Optional[int]:
    """Parse an `iso` label like ``"800"`` → 800. ``"Auto"`` → ``None``."""
    s = label.strip()
    if s.lower() in _NON_NUMERIC_ISO:
        return None
    if not re.fullmatch(r"[0-9]+", s):
        return None
    return int(s)


def _aperture_label_value(label: str) -> Optional[float]:
    """Parse an `f-number` label like ``"f/5.6"`` → 5.6."""
    s = label.strip()
    m = re.fullmatch(r"[fF]/([0-9]*\.?[0-9]+)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _nearest(
    value: float,
    choices: Iterable[str],
    parse,
) -> Optional[str]:
    """Return the label whose parsed value is nearest to ``value``.

    ``parse`` maps a label → numeric value or ``None`` (excluded). When the
    requested value is out of range, the extreme label still wins (the min
    over absolute distance clamps naturally). Returns ``None`` if no label
    parses to a number.
    """
    candidates: List[Tuple[float, str]] = []
    for label in choices:
        v = parse(label)
        if v is None:
            continue
        candidates.append((abs(v - value), label))
    if not candidates:
        return None
    # Stable: ties resolve to the first listed choice.
    return min(candidates, key=lambda t: t[0])[1]


# --- public API: value → nearest label ---

def seconds_to_label(seconds: float, choices: Iterable[str]) -> Optional[str]:
    """Nearest `shutterspeed` label for a shutter time in seconds.

    ``Bulb`` / ``Time`` are excluded as match targets.
    """
    return _nearest(float(seconds), choices, _shutter_label_value)


def iso_to_label(iso: int, choices: Iterable[str]) -> Optional[str]:
    """Nearest `iso` label for an ISO speed. ``Auto`` is excluded."""
    return _nearest(float(iso), choices, _iso_label_value)


def aperture_to_label(aperture: float, choices: Iterable[str]) -> Optional[str]:
    """Nearest `f-number` label for an aperture f-number."""
    return _nearest(float(aperture), choices, _aperture_label_value)


# --- public API: label → value (inverse, used by status()) ---

def label_to_seconds(label: str) -> Optional[float]:
    """`shutterspeed` label → seconds, or ``None`` for ``Bulb``/``Time``."""
    return _shutter_label_value(label)


def label_to_iso(label: str) -> Optional[int]:
    """`iso` label → int, or ``None`` for ``Auto``."""
    return _iso_label_value(label)


def label_to_aperture(label: str) -> Optional[float]:
    """`f-number` label → float f-number, or ``None`` if unparseable."""
    return _aperture_label_value(label)
