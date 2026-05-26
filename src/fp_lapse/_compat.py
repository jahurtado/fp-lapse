"""Python 3.10+ compatibility shims for upstream libraries.

`construct==2.8.8` (transitive dependency of `sigma-ptpy` via `ptpy`) still
references `collections.Sequence` (and friends), which were removed from the
top-level `collections` namespace in Python 3.10 — they now live exclusively
under `collections.abc`. Bookworm ships Python 3.11, so the library breaks at
the first PTP message round-trip.

This module re-injects the aliases. Call `patch_collections_abc()` once,
before importing any code that pulls in `construct` (i.e. before importing
`sigma_ptpy`).

When the upstream chain (construct → ptpy → sigma-ptpy) finally upgrades,
this shim becomes a no-op and can be deleted.
"""

from __future__ import annotations

import collections
import collections.abc

_ABC_NAMES = (
    "Mapping", "MutableMapping",
    "Sequence", "MutableSequence",
    "Set", "MutableSet",
    "Iterable", "Iterator",
    "Container", "Callable",
    "Hashable", "Sized",
    "KeysView", "ItemsView", "ValuesView",
)


def patch_collections_abc() -> None:
    for name in _ABC_NAMES:
        if not hasattr(collections, name):
            setattr(collections, name, getattr(collections.abc, name))
