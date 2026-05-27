"""Real adapter for the Sigma fp via sigma-ptpy.

Implements the `Camera` Protocol using the lessons learned in the manual
validation session of 2026-05-20:

- Always instantiate `SigmaPTPy(ignore_events=True)`.
- Call `config_api()` immediately after opening the session — without it,
  vendor-specific reads return empty payloads.
- Force `DestToSave=InCamera` at connect time so shots land on the SD card.
- Do NOT call `close_application()` between shots; it makes the fp drop off
  the USB bus until you physically reconnect. The session is kept open for
  the entire program run.
- Treat both `ImageGenCompleted` and `ImageDataStorageCompleted` as terminal
  success states for capture (in InCamera mode the fp does not report the
  latter even though the file is on SD).
- All exposed values are human-friendly; APEX byte encoding is handled here
  via the converters in `sigma_ptpy.apex`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

from .. import _compat
_compat.patch_collections_abc()

from sigma_ptpy import SigmaPTPy  # noqa: E402
from sigma_ptpy.apex import (  # noqa: E402
    Aperture3Converter,
    ISOSpeedConverter,
    ShutterSpeed3Converter,
)
from sigma_ptpy.enum import (  # noqa: E402
    CaptureMode as _CaptureMode,
    CaptStatus as _CaptStatus,
    DestToSave as _DestToSave,
    ExposureMode as _ExposureMode,
    FocusMode as _FocusMode,
    ISOAuto as _ISOAuto,
)
from sigma_ptpy.schema import (  # noqa: E402
    CamDataGroup1,
    CamDataGroup2,
    CamDataGroup3,
    CamDataGroupFocus,
    SnapCommand,
)

from .iface import (  # noqa: E402
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


_EXP_TO_SIGMA = {
    ExposureMode.PROGRAM: _ExposureMode.ProgramAuto,
    ExposureMode.APERTURE_PRIORITY: _ExposureMode.AperturePriority,
    ExposureMode.SHUTTER_PRIORITY: _ExposureMode.ShutterPriority,
    ExposureMode.MANUAL: _ExposureMode.Manual,
}
_EXP_FROM_SIGMA = {v: k for k, v in _EXP_TO_SIGMA.items()}

_FOCUS_TO_SIGMA = {
    FocusMode.MF: _FocusMode.MF,
    FocusMode.AF_S: _FocusMode.AF_S,
    FocusMode.AF_C: _FocusMode.AF_C,
}
_FOCUS_FROM_SIGMA = {v: k for k, v in _FOCUS_TO_SIGMA.items()}

_OK_TERMINAL = {
    _CaptStatus.ImageGenCompleted,
    _CaptStatus.ImageDataStorageCompleted,
}
_FAIL_TERMINAL = {
    _CaptStatus.Interrupted, _CaptStatus.AFFailed, _CaptStatus.BufferFull,
    _CaptStatus.CWBFailed, _CaptStatus.ImageGenFailed, _CaptStatus.Failed,
}

# How often we re-ask the camera "are you done yet?" after firing the
# shutter. PTP roundtrip on the fp is ~30 ms, so 20 ms means we're
# pretty much issuing back-to-back queries — we catch each CaptStatus
# transition with minimal lag. Field trace 2026-05-21 showed the fp
# transitions between states in 500 ms+ blocks, so even 50 ms polling
# was over-sampling — but the win comes from detecting the *terminal*
# state quickly (less idle wait after the shot is actually done).
_POLL_INTERVAL_S = 0.02


class SigmaFpCamera:
    """Real `Camera` adapter for the Sigma fp."""

    def __init__(self) -> None:
        self._cam: Optional[SigmaPTPy] = None
        # Serialises all PTP operations. The engine scheduler calls
        # shoot() / status() while a separate camera-health thread
        # may try connect() in parallel — without this lock the
        # libusb session can be corrupted.
        self._lock = threading.RLock()
        # Cached parameters from the most recent `set_params` call.
        # We use these to populate `CaptureResult` after a successful
        # `shoot()` instead of issuing a full status() roundtrip — the
        # engine doesn't read CaptureResult and shaving the pre-shoot
        # PTP latency makes brackets noticeably faster.
        self._last_shutter_s: Optional[float] = None
        self._last_aperture: Optional[float] = None
        self._last_iso: Optional[int] = None
        # Cached focus mode. Driven both by `set_params(focus_mode=…)`
        # and by lazy refresh on first shoot. The user can also toggle
        # the AF/MF switch on the camera body — that won't fire
        # `set_params`, so on each shoot we double-check by reading the
        # one-PTP-call focus group (much cheaper than full status()).
        self._cached_focus_mode: Optional[FocusMode] = None

    # --- lifecycle ---
    def _mark_disconnected(self, exc: Exception) -> CameraNotConnected:
        """Drop the PTP session reference after a transport error.

        Called from any PTP-issuing method when libusb / ptpy reports
        the device is gone (Errno 19 `No such device`, broken pipe,
        timeout). The camera-health thread will retry `connect()`.
        """
        self._cam = None
        self._cached_focus_mode = None
        return CameraNotConnected(f"transport error: {exc}")

    def connect(self) -> None:
        with self._lock:
            if self._cam is not None:
                return
            self._connect_locked()

    def _connect_locked(self) -> None:
        cam = SigmaPTPy(ignore_events=True)
        cam.open_session()
        cam.config_api()
        cam.set_cam_data_group3(CamDataGroup3(DestToSave=_DestToSave.InCamera))
        # Force ExposureMode=Manual so the shutter / aperture / ISO we
        # push via set_params are respected. In ProgramAuto (the default
        # if the user has the dial on P) the fp silently overrides our
        # shutter request with its own metering — observed in the field
        # 2026-05-21: a "1/30s" shot exposing for 0.4s because the
        # camera was metering Program. Manual is the only mode where
        # all three params are honoured deterministically.
        try:
            cam.set_cam_data_group2(
                CamDataGroup2(ExposureMode=_ExposureMode.Manual))
            logger.info("sigma_fp: forced ExposureMode=Manual on connect")
        except Exception as e:
            logger.warning(
                "sigma_fp: could not force Manual exposure mode: %s — "
                "shutter/iso/aperture settings may be ignored", e,
            )
        # Drain stale entries from the camera's image DB so the first shoot()
        # in this session can use a known image_id. The fp accumulates these
        # across power cycles; left over from a previous session they make
        # capt_status return stale terminal states.
        self._cam = cam
        try:
            self._drain_image_db()
        except Exception:
            # Not fatal — shoot() will still work, just may need to skip
            # ahead through stale capt_status values.
            pass

    def _drain_image_db(self) -> None:
        cam = self._require()
        status = cam.get_cam_capt_status(0)
        head, tail = status.ImageDBHead, status.ImageDBTail
        for image_id in range(head, tail):
            try:
                cam.clear_image_db_single(image_id)
            except Exception:
                pass

    def disconnect(self) -> None:
        with self._lock:
            if self._cam is None:
                return
            try:
                self._cam.close_session()
            except Exception:
                pass
            self._cam = None
            self._cached_focus_mode = None

    def is_connected(self) -> bool:
        return self._cam is not None

    def _require(self) -> SigmaPTPy:
        if self._cam is None:
            raise CameraNotConnected("SigmaFpCamera is not connected")
        return self._cam

    # --- liveness ---
    def probe(self) -> None:
        """Liveness round-trip for the health watchdog (see Camera.probe).

        `get_device_info()` is a real PTP round-trip on the fp that fails
        when the body is gone, so it reliably detects a silent disconnect.
        """
        with self._lock:
            cam = self._require()
            try:
                cam.get_device_info()
            except Exception as e:
                raise self._mark_disconnected(e)

    # --- introspection ---
    def info(self) -> CameraInfo:
        with self._lock:
            cam = self._require()
            try:
                di = cam.get_device_info()
            except Exception as e:
                raise self._mark_disconnected(e)
            return CameraInfo(
                model=str(di.Model),
                firmware=str(di.DeviceVersion),
                serial=str(di.SerialNumber),
            )

    def status(self) -> CameraStatus:
        with self._lock:
            cam = self._require()
            try:
                g1 = cam.get_cam_data_group1()
                g2 = cam.get_cam_data_group2()
                focus = cam.get_cam_data_group_focus()
            except Exception as e:
                raise self._mark_disconnected(e)

            shutter_s = (ShutterSpeed3Converter.decode_uint8(g1.ShutterSpeed)
                         if g1.ShutterSpeed is not None else None)
            aperture = (Aperture3Converter.decode_uint8(g1.Aperture)
                        if g1.Aperture is not None else None)
            iso = (ISOSpeedConverter.decode_uint8(g1.ISOSpeed)
                   if g1.ISOSpeed is not None else None)
            iso_auto = (g1.ISOAuto == _ISOAuto.Auto) if g1.ISOAuto is not None else False

            return CameraStatus(
                shutter_s=shutter_s,
                aperture=aperture,
                iso=iso,
                iso_auto=iso_auto,
                exposure_mode=_EXP_FROM_SIGMA.get(g2.ExposureMode) if g2.ExposureMode else None,
                focus_mode=_FOCUS_FROM_SIGMA.get(focus.FocusMode) if focus.FocusMode else None,
                battery_pct=None,
                sd_free_bytes=None,
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
        # Per-parameter "auto" no longer exists in the data model —
        # auto/manual is a config-level decision (empty `shots` tuple).
        # Each call passes numerics, the engine selects `exposure_mode`.

        # ExposureMode must be set BEFORE shutter/aperture/iso. In ProgramAuto
        # the camera silently rounds/ignores shutter requests; the value we
        # encode does not match what get_cam_data_group1 reports back.
        with self._lock:
            cam = self._require()

            try:
                if exposure_mode is not None:
                    cam.set_cam_data_group2(
                        CamDataGroup2(ExposureMode=_EXP_TO_SIGMA[exposure_mode]))

                if focus_mode is not None:
                    cam.set_cam_data_group_focus(
                        CamDataGroupFocus(FocusMode=_FOCUS_TO_SIGMA[focus_mode]))
                    self._cached_focus_mode = focus_mode

                g1_kwargs: dict = {}
                if shutter_s is not None:
                    g1_kwargs["ShutterSpeed"] = ShutterSpeed3Converter.encode_uint8(shutter_s)
                    self._last_shutter_s = shutter_s
                if aperture is not None:
                    g1_kwargs["Aperture"] = Aperture3Converter.encode_uint8(aperture)
                    self._last_aperture = aperture
                if iso is not None:
                    g1_kwargs["ISOSpeed"] = ISOSpeedConverter.encode_uint8(iso)
                    g1_kwargs["ISOAuto"] = _ISOAuto.Manual
                    self._last_iso = iso
                if g1_kwargs:
                    cam.set_cam_data_group1(CamDataGroup1(**g1_kwargs))
            except Exception as e:
                raise self._mark_disconnected(e)

    # --- capture ---
    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        with self._lock:
            return self._shoot_locked(timeout_s)

    def _shoot_locked(self, timeout_s: float) -> CaptureResult:
        cam = self._require()

        # The lens focus mode can be flipped on the camera body without
        # going through set_params, so we re-read it each shoot. The
        # one-PTP-call focus group (~30 ms) is much lighter than the
        # full status() that previously sat on the hot path.
        try:
            focus = cam.get_cam_data_group_focus()
        except Exception as e:
            raise self._mark_disconnected(e)
        sigma_focus = focus.FocusMode if focus is not None else None
        focus_mode = _FOCUS_FROM_SIGMA.get(sigma_focus) if sigma_focus else None
        self._cached_focus_mode = focus_mode

        # Dispatch the capture mode to match the lens focus mode. With a
        # manual-focus lens (or AF/MF switch in MF), GeneralCapt still
        # tries to drive AF and the camera returns CaptStatus=AFFailed —
        # i.e. no exposure happens at all. NonAFCapt skips the AF step
        # and fires the shutter directly.
        capture_mode = (
            _CaptureMode.NonAFCapt
            if focus_mode == FocusMode.MF
            else _CaptureMode.GeneralCapt
        )

        # The new snap's image_id will be the current DBTail. Capture it
        # BEFORE snap_command so we know which slot to poll afterwards.
        try:
            pre = cam.get_cam_capt_status(0)
        except Exception as e:
            raise self._mark_disconnected(e)
        new_image_id = pre.ImageDBTail

        t0 = time.monotonic()
        try:
            cam.snap_command(SnapCommand(CaptureMode=capture_mode))
        except Exception as e:
            raise self._mark_disconnected(e)

        # Trace each CaptStatus transition so we can post-mortem which
        # phase eats the most time per shot. Format:
        # "shoot trace id=N: 50ms=AFInProgress 120ms=ShootInProgress ..."
        trace: list[tuple[float, str]] = []
        last_name: Optional[str] = None
        # We swallow transient errors in the poll loop (one missed
        # status() read shouldn't abort the capture), but we cap the
        # consecutive count — past that we're clearly disconnected and
        # there's no point hammering the dead bus until the outer
        # timeout fires.
        poll_errors = 0
        max_poll_errors = max(3, int(0.3 / _POLL_INTERVAL_S))

        deadline = t0 + timeout_s
        last_status = None
        while time.monotonic() < deadline:
            try:
                status = cam.get_cam_capt_status(new_image_id)
                poll_errors = 0
            except Exception as e:
                poll_errors += 1
                if poll_errors >= max_poll_errors:
                    raise self._mark_disconnected(e)
                time.sleep(_POLL_INTERVAL_S)
                continue
            last_status = status
            name = status.CaptStatus.name if status.CaptStatus is not None else "None"
            if name != last_name:
                trace.append((time.monotonic() - t0, name))
                last_name = name
            if status.CaptStatus in _OK_TERMINAL:
                # Free the slot so it doesn't accumulate in the camera DB.
                try:
                    cam.clear_image_db_single(new_image_id)
                except Exception:
                    pass
                logger.info(
                    "sigma_fp: shoot trace id=%d %s",
                    new_image_id,
                    " ".join(f"{int(t*1000)}ms={n}" for t, n in trace),
                )
                return CaptureResult(
                    shutter_s=self._last_shutter_s or 0.0,
                    aperture=self._last_aperture or 0.0,
                    iso=self._last_iso or 0,
                    duration_s=time.monotonic() - t0,
                )
            if status.CaptStatus in _FAIL_TERMINAL:
                try:
                    cam.clear_image_db_single(new_image_id)
                except Exception:
                    pass
                logger.info(
                    "sigma_fp: shoot trace id=%d FAILED %s",
                    new_image_id,
                    " ".join(f"{int(t*1000)}ms={n}" for t, n in trace),
                )
                raise CaptureFailed(status.CaptStatus.name)
            time.sleep(_POLL_INTERVAL_S)

        last = last_status.CaptStatus.name if last_status else "no_status"
        raise CaptureFailed(
            f"timeout after {timeout_s}s (image_id={new_image_id}, "
            f"last status: {last})"
        )
