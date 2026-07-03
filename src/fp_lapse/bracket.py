"""Semiautomatic bracketing generator (pure algorithm).

Peer to `shutter.py`: hardware-free, no PIL, no `usb`/`gphoto2`. Given a
`BracketSpec` (a reference exposure plus a handful of ladder parameters)
and the discrete shutter grid (injected, never imported from `ui`), it
generates an exposure ladder as an ordinary `tuple[Shot, ...]` the engine
already consumes. See `docs/features/semiauto-bracketing/prd.md`.

Design highlights:

- The reference `Shot` is emitted **verbatim** at one end of the ladder
  (rung 0), so at least one shot always survives.
- Non-reference rungs choose a `(shutter, iso)` from the eligible ISO set
  `{iso1, iso2?}` that **minimises exposure time** while landing in the
  grid's `[lo, hi]` range; out-of-range rungs are **dropped**.
- Aperture is held constant at the reference's value across the ladder.
- Output is always ordered BRIGHTEST -> DARKEST (descending relative
  light), regardless of which end the reference anchors.

The grid is injected as `shutter_grid` (the UI/tests pass
`fp_lapse.ui.edit_values.SHUTTER_VALUES`) so this module never imports
from `ui` (no core <- ui layer inversion).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .configs import MAX_SHOTS_PER_BRACKET, Shot
from .shutter import SHUTTER_MAX_S, SHUTTER_MIN_S  # noqa: F401 (documented range)

# Same closed-range tolerance `shutter.in_range` uses.
_EPS: float = 1e-9


@dataclass(frozen=True)
class BracketSpec:
    """Inputs to the bracket generator.

    `reference` is a full `Shot` whose shutter/iso/aperture are already
    grid values. `brightest` is True when the reference is the BRIGHTEST
    (most-exposed) shot and the ladder extends darker, False when the
    reference is the DARKEST shot and the ladder extends brighter.
    `ev_step` is the EV between adjacent rungs (one of
    `{1, 2, 2.5, 3, 3.5, 4}` from the UI). `n` is the requested rung
    count (1..MAX_SHOTS_PER_BRACKET). `iso1` is mandatory; `iso2` is an
    optional second eligible ISO (None == "off").
    """

    reference: Shot
    brightest: bool
    ev_step: float
    n: int
    iso1: int
    iso2: Optional[int] = None


@dataclass(frozen=True)
class BracketResult:
    """Output of the bracket generator.

    `shots` are the surviving rungs, ordered BRIGHTEST -> DARKEST.
    `requested` echoes `spec.n`; `dropped` is how many requested rungs
    fell out of range.
    """

    shots: Tuple[Shot, ...]
    requested: int

    @property
    def dropped(self) -> int:
        return self.requested - len(self.shots)


def _snap_nearest(required: float, grid: Sequence[float]) -> float:
    """Snap `required` to the nearest grid value.

    On an exact distance tie, prefer the shorter shutter (smaller value).
    `grid` is assumed sorted ascending, so iterating and replacing only
    on a strictly-closer distance keeps the earlier (smaller) value on a
    tie.
    """
    best = grid[0]
    best_d = abs(grid[0] - required)
    for v in grid[1:]:
        d = abs(v - required)
        if d < best_d:
            best = v
            best_d = d
    return best


def _eligible_isos(spec: BracketSpec) -> List[int]:
    """Eligible ISO set for non-reference rungs: {iso1} + {iso2?}.

    The reference ISO is NOT automatically eligible — it governs only
    rung 0. Duplicates (iso1 == iso2) are collapsed.
    """
    isos: List[int] = [spec.iso1]
    if spec.iso2 is not None and spec.iso2 != spec.iso1:
        isos.append(spec.iso2)
    return isos


def generate_bracket(
    spec: BracketSpec, *, shutter_grid: Sequence[float],
) -> BracketResult:
    """Generate the exposure ladder for `spec`.

    `shutter_grid` is the discrete shutter scale (assumed sorted
    ascending, in seconds). Returns a `BracketResult` whose `shots` are
    ordered BRIGHTEST -> DARKEST.
    """
    grid = list(shutter_grid)
    if not grid:
        raise ValueError("shutter_grid must not be empty")
    lo, hi = grid[0], grid[-1]
    ref = spec.reference
    light_ref = ref.shutter * ref.iso
    isos = _eligible_isos(spec)

    n = min(spec.n, MAX_SHOTS_PER_BRACKET)

    # (target_light, Shot|None) per rung, in k order.
    rungs: List[Tuple[float, Optional[Shot]]] = []
    for k in range(n):
        if k == 0:
            # Reference rung — emitted verbatim, never re-optimised.
            rungs.append((light_ref, Shot(
                shutter=ref.shutter, iso=ref.iso, aperture=ref.aperture,
            )))
            continue
        if spec.brightest:
            target = light_ref / (2.0 ** (spec.ev_step * k))
        else:
            target = light_ref * (2.0 ** (spec.ev_step * k))
        # Collect feasible (snapped_shutter, iso) candidates.
        candidates: List[Tuple[float, int]] = []
        for g in isos:
            required = target / g
            if lo - _EPS <= required <= hi + _EPS:
                candidates.append((_snap_nearest(required, grid), g))
        if not candidates:
            rungs.append((target, None))
            continue
        # Minimise exposure time (shortest snapped shutter); tie-break
        # prefers the lower ISO (less noise).
        snapped, g = min(candidates, key=lambda c: (c[0], c[1]))
        rungs.append((target, Shot(
            shutter=snapped, iso=g, aperture=ref.aperture,
        )))

    # Emit BRIGHTEST -> DARKEST == descending target light. Dropped
    # rungs leave gaps that close up.
    surviving = [(t, s) for (t, s) in rungs if s is not None]
    surviving.sort(key=lambda ts: ts[0], reverse=True)
    return BracketResult(
        shots=tuple(s for (_, s) in surviving),
        requested=spec.n,
    )
