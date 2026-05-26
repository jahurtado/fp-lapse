"""Camera abstraction layer.

Two implementations satisfy the `Camera` Protocol:

  - `SigmaFpCamera` — real adapter via sigma-ptpy. Pi-only (USB hardware).
  - `MockCamera` — in-memory fake. Use for tests and Mac development.

Note: `SigmaFpCamera` is NOT imported at package level on purpose — it pulls
sigma-ptpy → pyusb → libusb, which is not available on a vanilla Mac. Import
it explicitly when you need it:

    from fp_lapse.camera.sigma_fp import SigmaFpCamera
"""

from .iface import (
    Camera,
    CameraBusy,
    CameraError,
    CameraInfo,
    CameraNotConnected,
    CameraStatus,
    CaptureFailed,
    CaptureResult,
    ExposureMode,
    FocusMode,
)
from .mock import MockCamera

__all__ = [
    "Camera",
    "CameraBusy",
    "CameraError",
    "CameraInfo",
    "CameraNotConnected",
    "CameraStatus",
    "CaptureFailed",
    "CaptureResult",
    "ExposureMode",
    "FocusMode",
    "MockCamera",
]
