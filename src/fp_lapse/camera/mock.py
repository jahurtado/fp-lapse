"""In-memory fake camera.

Use this for unit tests and for developing the rest of the system on a Mac
without touching the real Sigma fp or the Raspberry Pi.

Minimal by design: tracks the params last written, sleeps approximately
`shutter_s + 0.5s` on `shoot()` to mimic real timing, counts shots, and
exposes a `injected_failures` hook so engine tests can verify error paths
without needing a flaky camera.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from .iface import (
    ApertureParam,
    CameraInfo,
    CameraNotConnected,
    CameraStatus,
    CaptureFailed,
    CaptureResult,
    ExposureMode,
    FocusMode,
    IsoParam,
    ShutterParam,
)

_log = logging.getLogger(__name__)


class MockCamera:
    """Fake camera satisfying the `Camera` Protocol."""

    def __init__(
        self,
        *,
        model: str = "MOCK fp",
        firmware: str = "0.0",
        serial: str = "MOCK-0000",
        battery_pct: int = 80,
        sd_free_bytes: int = 32 * 1024 * 1024 * 1024,  # 32 GB
        shutter_s: float = 1 / 250,
        aperture: float = 5.6,
        iso: int = 100,
        sleep_overhead_s: float = 0.5,
    ) -> None:
        self._connected = False
        self._info = CameraInfo(model=model, firmware=firmware, serial=serial)
        self._battery_pct = battery_pct
        self._sd_free_bytes = sd_free_bytes
        self._shutter_s: Optional[float] = shutter_s
        self._aperture: Optional[float] = aperture
        self._iso: Optional[int] = iso
        self._exposure_mode: Optional[ExposureMode] = ExposureMode.MANUAL
        self._focus_mode: Optional[FocusMode] = FocusMode.AF_S
        self._sleep_overhead_s = sleep_overhead_s

        # Test hooks.
        self.shots_taken: int = 0
        self.injected_failures: List[str] = []

    # --- lifecycle ---
    def connect(self) -> None:
        _log.info("MockCamera.connect()")
        self._connected = True

    def disconnect(self) -> None:
        _log.info("MockCamera.disconnect()")
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _require(self) -> None:
        if not self._connected:
            raise CameraNotConnected("mock camera is not connected")

    # --- introspection ---
    def info(self) -> CameraInfo:
        self._require()
        return self._info

    def status(self) -> CameraStatus:
        self._require()
        return CameraStatus(
            shutter_s=self._shutter_s,
            aperture=self._aperture,
            iso=self._iso,
            iso_auto=False,
            exposure_mode=self._exposure_mode,
            focus_mode=self._focus_mode,
            battery_pct=self._battery_pct,
            sd_free_bytes=self._sd_free_bytes,
        )

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
        self._require()
        _log.info(
            "MockCamera.set_params(shutter_s=%s, aperture=%s, iso=%s, "
            "exposure_mode=%s, focus_mode=%s)",
            shutter_s, aperture, iso, exposure_mode, focus_mode,
        )
        if shutter_s is not None:
            self._shutter_s = float(shutter_s)
        if aperture is not None:
            self._aperture = float(aperture)
        if iso is not None:
            self._iso = int(iso)
        if exposure_mode is not None:
            self._exposure_mode = exposure_mode
        if focus_mode is not None:
            self._focus_mode = focus_mode

    # --- capture ---
    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        self._require()
        if self.injected_failures:
            reason = self.injected_failures.pop(0)
            _log.info("MockCamera.shoot() injected failure: %s", reason)
            raise CaptureFailed(reason)
        sleep_s = min(
            (self._shutter_s or 0.0) + self._sleep_overhead_s,
            max(0.0, timeout_s - 0.05),
        )
        _log.info("MockCamera.shoot() (sleeping %.2fs)", sleep_s)
        if sleep_s > 0:
            time.sleep(sleep_s)
        self.shots_taken += 1
        return CaptureResult(
            shutter_s=self._shutter_s or 0.0,
            aperture=self._aperture or 0.0,
            iso=self._iso or 0,
            duration_s=sleep_s,
        )
