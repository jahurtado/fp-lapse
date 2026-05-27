"""Runtime hot-swap proxy — a `Camera` that swaps its inner adapter.

`CameraProxy` satisfies the `Camera` Protocol and holds the current concrete
adapter (`self._inner`) plus its kind (`self._kind`), delegating every
Protocol method to the inner adapter. `_build_camera()` returns this proxy,
and the same object is handed to `Engine`, `App` and `CameraHealth` — they
keep a stable reference, so no engine/app/scheduler/health changes are
needed.

The hot-swap lives entirely in `connect()`, which the `camera_health.py`
reconnect loop already calls whenever the camera is lost:

1. Read USB descriptors and resolve the kind (`detector()`; descriptor read
   only — never opens a PTP session).
2. If the kind **differs** from `self._kind` (or there is no inner adapter
   yet): **fully `disconnect()` the old inner adapter first** — release its
   USB grab before the new adapter enumerates — then build and store the new
   adapter for the new kind.
3. If the kind is unchanged, keep the existing inner adapter.
4. Call `self._inner.connect()`.

Concurrency is deliberately simple: the inner reference is read/swapped under
a short lock (`self._swap_lock`), held only for the assignment — never across
I/O. Each concrete adapter has its own `RLock` serialising its
`shoot()`/`disconnect()`, so a swap racing an active capture can at worst make
that shot fail (the engine already counts it as a failure/skip). No
pause/coordinate-with-capture logic.

The `detector` and `factory` are injectable (with real defaults wiring
`detect.py` + lazy adapter imports) so the proxy is unit-testable on the Mac
with no `usb`/`gphoto2`.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from . import detect
from .iface import (
    ApertureParam,
    CameraInfo,
    CameraNotConnected,
    CameraStatus,
    CaptureResult,
    ExposureMode,
    FocusMode,
    IsoParam,
    ShutterParam,
)

logger = logging.getLogger(__name__)


def _default_detector() -> str:
    """Resolve the camera kind from the live environment + USB bus.

    Reads `FP_LAPSE_CAMERA` / `FP_LAPSE_MOCK`, the platform, and the USB
    descriptors — descriptor read only, never opening a session.
    """
    import os
    import sys

    override = detect.override_from_env(os.environ)
    is_darwin = sys.platform == "darwin"
    # Skip USB enumeration entirely when the answer is already decided —
    # avoids importing pyusb on the Mac dev path.
    if override is not None or is_darwin:
        return detect.select_camera_kind([], override=override, is_darwin=is_darwin)
    devices = detect.enumerate_usb_ids()
    return detect.select_camera_kind(devices, override=override, is_darwin=is_darwin)


def _short_model_label(model: str) -> str:
    """Collapse a camera model string to a compact status-bar label.

    "SIGMA fp" → "fp"; "Nikon D5600" → "D5600"; otherwise the last
    whitespace-separated token (kept short for the ~22 px label slot).
    """
    m = (model or "").strip()
    low = m.lower()
    if "d5600" in low:
        return "D5600"
    if "fp" in low:
        return "fp"
    return m.split()[-1] if m else "fp"


def _default_factory(kind: str):
    """Lazy-import and instantiate the adapter for `kind`.

    The Nikon and Sigma adapters pull `gphoto2` / `sigma-ptpy` respectively,
    which aren't importable on a vanilla Mac — hence the per-kind lazy import
    here rather than at module top.
    """
    if kind == detect.KIND_MOCK:
        from .mock import MockCamera
        return MockCamera(sleep_overhead_s=0.0)
    if kind == detect.KIND_NIKON:
        from .nikon_gphoto import NikonGPhotoCamera
        return NikonGPhotoCamera()
    if kind == detect.KIND_SIGMA:
        from .sigma_fp import SigmaFpCamera
        return SigmaFpCamera()
    raise ValueError(f"unknown camera kind: {kind!r}")


class CameraProxy:
    """`Camera`-Protocol proxy with runtime adapter hot-swap."""

    def __init__(
        self,
        *,
        detector: Callable[[], str] = _default_detector,
        factory: Callable[[str], object] = _default_factory,
    ) -> None:
        self._detector = detector
        self._factory = factory
        self._inner = None  # type: Optional[object]
        self._kind: Optional[str] = None
        # Short status-bar label for the live camera ("fp" / "D5600"),
        # refreshed after a successful connect so the UI render path can
        # read it cheaply without I/O. Defaults to the legacy "fp".
        self._model_label: str = "fp"
        # Guards the (kind, inner) reference pair. Held only for the
        # reassignment, never across adapter I/O.
        self._swap_lock = threading.Lock()

    # --- internal ---
    def _get_inner(self):
        with self._swap_lock:
            return self._inner

    def _require(self):
        inner = self._get_inner()
        if inner is None:
            raise CameraNotConnected("no camera detected")
        return inner

    # --- lifecycle ---
    def connect(self) -> None:
        """Re-detect by VID/PID, swap the inner adapter if the kind changed,
        then connect it. Called both at startup and by every health tick."""
        kind = self._detector()

        with self._swap_lock:
            current_inner = self._inner
            current_kind = self._kind
            need_swap = current_inner is None or kind != current_kind

        # NOTE: the check-then-act below (the lock is released during
        # disconnect()/factory()) is safe ONLY because connect() has a single
        # caller after boot — the camera-health thread's serial reconnect loop,
        # and the boot connect() completes before that thread starts. Two
        # concurrent connect()s could both decide to swap and build two
        # adapters, leaking one. If a second concurrent caller is ever added,
        # guard the whole detect→disconnect→build→connect sequence.
        if need_swap:
            # Release the old body's USB grab BEFORE building the new
            # adapter, so the new one can enumerate without contention.
            if current_inner is not None:
                logger.info(
                    "camera proxy: kind change %s → %s, disconnecting old adapter",
                    current_kind, kind,
                )
                try:
                    current_inner.disconnect()
                except Exception as e:
                    logger.warning("camera proxy: error disconnecting old adapter: %s", e)
            new_inner = self._factory(kind)
            with self._swap_lock:
                self._inner = new_inner
                self._kind = kind
            logger.info("camera proxy: now hosting kind=%s", kind)
            inner = new_inner
        else:
            inner = current_inner

        inner.connect()
        # Refresh the cached UI label from the now-connected adapter. Best
        # effort: info() is one cheap call here (post-connect), not on the
        # hot render path. A failure leaves the previous label in place.
        try:
            self._model_label = _short_model_label(inner.info().model)
        except Exception:
            pass

    def disconnect(self) -> None:
        inner = self._get_inner()
        if inner is not None:
            inner.disconnect()

    def is_connected(self) -> bool:
        # Must stay cheap and non-blocking for the health probe: read the
        # reference, ask the adapter (its own is_connected() is non-I/O).
        inner = self._get_inner()
        if inner is None:
            return False
        return inner.is_connected()

    def current_kind(self) -> Optional[str]:
        with self._swap_lock:
            return self._kind

    # --- cheap UI reads (no I/O; safe on the render hot path) ---
    def model_label(self) -> str:
        """Short status-bar label for the live camera ("fp" / "D5600").

        Cached at connect time so the UI render path stays I/O-free. Read here
        (UI thread) and written in connect() (health thread) without the swap
        lock — intentional: a `str` rebind is atomic under CPython's GIL and a
        one-tick-stale label is harmless.
        """
        return self._model_label

    def dial_mismatch(self) -> bool:
        """Whether the live adapter reports its exposure dial in the wrong
        mode (D5600 only). Adapters without the concept report False."""
        inner = self._get_inner()
        if inner is None:
            return False
        return bool(getattr(inner, "dial_mismatch", False))

    # --- liveness ---
    def probe(self) -> None:
        self._require().probe()

    # --- introspection ---
    def info(self) -> CameraInfo:
        return self._require().info()

    def status(self) -> CameraStatus:
        return self._require().status()

    # --- configuration ---
    def set_params(
        self,
        *,
        shutter_s: ShutterParam = None,
        aperture: ApertureParam = None,
        iso: IsoParam = None,
        exposure_mode: Optional[ExposureMode] = None,
        focus_mode: Optional[FocusMode] = None,
    ) -> None:
        self._require().set_params(
            shutter_s=shutter_s,
            aperture=aperture,
            iso=iso,
            exposure_mode=exposure_mode,
            focus_mode=focus_mode,
        )

    # --- capture ---
    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        return self._require().shoot(timeout_s)
