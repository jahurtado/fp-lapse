"""Camera abstraction — interface and shared types.

The `Camera` Protocol is what every adapter (real Sigma fp, mock for tests)
satisfies. The rest of the system depends only on this interface, never on
`sigma_ptpy` or any GPIO/USB detail. That keeps the engine and UI testable
on a Mac with no hardware attached.

All values exposed by this interface are human-friendly: shutter in seconds,
aperture as an f-number, ISO as the speed value. The real adapter translates
to/from Sigma's APEX-encoded byte values internally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

# Per-parameter values accepted by `set_params`. `None` means "don't
# include this kwarg in the PTP call" (i.e. leave the camera as it is).
# "Auto" exposure lives at the config level
# (`TimelapseConfig.shots == ()`), so adapter set_params no longer
# accepts a per-parameter "auto" sentinel.
ShutterParam = Optional[float]
IsoParam = Optional[int]
ApertureParam = Optional[float]


class ExposureMode(Enum):
    PROGRAM = "program"
    APERTURE_PRIORITY = "aperture_priority"
    SHUTTER_PRIORITY = "shutter_priority"
    MANUAL = "manual"


class FocusMode(Enum):
    MF = "mf"
    AF_S = "af_s"
    AF_C = "af_c"


@dataclass(frozen=True)
class CameraInfo:
    model: str
    firmware: str
    serial: str


@dataclass(frozen=True)
class CameraStatus:
    """Snapshot of camera state at a given instant."""

    shutter_s: Optional[float]
    aperture: Optional[float]
    iso: Optional[int]
    iso_auto: bool
    exposure_mode: Optional[ExposureMode]
    focus_mode: Optional[FocusMode]
    battery_pct: Optional[int] = None
    sd_free_bytes: Optional[int] = None


@dataclass(frozen=True)
class CaptureResult:
    """What was captured. Returned by `Camera.shoot()` on success."""

    shutter_s: float
    aperture: float
    iso: int
    duration_s: float


class CameraError(Exception):
    """Base class for all camera errors."""


class CameraNotConnected(CameraError):
    """The camera must be connected for the requested operation."""


class CameraBusy(CameraError):
    """Camera rejected the command because a previous one is still in flight."""


class CaptureFailed(CameraError):
    """Snap was issued but the camera reported failure (AFFailed, BufferFull, ...).

    `reason` is the camera's terminal-state name (e.g. "AFFailed", "Failed",
    "ImageGenFailed") or a transport-level description on timeout.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Camera(Protocol):
    """All camera adapters satisfy this interface."""

    # --- lifecycle ---
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...

    def probe(self) -> None:
        """Cheap transport round-trip used by the health watchdog.

        `camera_health` calls this on every tick to detect a *silent*
        disconnect (USB pulled / camera asleep while the engine is idle).
        It MUST hit the device and raise `CameraNotConnected` if the body
        is gone — it must NOT be satisfiable by a cached read (a cached
        read would let a disconnect go unnoticed). Returns None on success.

        `info()` / `status()` are for reading data, not liveness; the
        liveness contract lives here so each adapter can choose the
        cheapest call that actually reaches the device.
        """
        ...

    # --- introspection ---
    def info(self) -> CameraInfo: ...
    def status(self) -> CameraStatus: ...

    # --- configuration (atomic where supported) ---
    def set_params(
        self,
        *,
        shutter_s: ShutterParam = None,
        aperture: ApertureParam = None,
        iso: IsoParam = None,
        exposure_mode: Optional[ExposureMode] = None,
        focus_mode: Optional[FocusMode] = None,
    ) -> None: ...

    # --- capture ---
    def shoot(self, timeout_s: float = 10.0) -> CaptureResult: ...
