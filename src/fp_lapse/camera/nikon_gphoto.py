"""Real adapter for the Nikon D5600 via gphoto2 / libgphoto2.

Second concrete `Camera` adapter alongside `SigmaFpCamera`. It mirrors the
Sigma adapter's discipline learned on hardware:

- A single `threading.RLock` serialises **all** operations. The engine
  scheduler calls `set_params()` / `shoot()` / `status()` while
  `camera_health.py` may call `connect()` / `probe()` / `is_connected()`
  concurrently — the lock keeps the libgphoto2 session from being corrupted.
- The gphoto2 camera handle is **opened once at `connect()` and kept open**
  for the whole run, never per shot (same lifecycle as the Sigma's PTP
  session). `capturetarget` is forced to the SD card so shots are saved
  on-camera, not downloaded to the 1 GB Pi.
- `_mark_disconnected(exc)` drops the handle on any transport error so
  `camera_health.py` recovers by calling `connect()` on its next tick.

Two D5600-specific facts drive the design (confirmed in the 2026-05-27 spike):

1. The exposure-mode dial (`expprogram`) is **read-only over USB**
   (`Readonly: 1`). `set_params(exposure_mode=…)` therefore never writes it;
   it reads the dial and, on a mismatch with what the engine asked for, logs
   a WARNING and records `self._dial_mismatch` so `status()` / the UI can
   show "DIAL NOT ON M". Manual (dial on M) is the primary eclipse path.
2. gphoto2 must run **as root**; as user `pi`, `libusb_open` fails with
   `Access denied (-3)`, surfaced as a misleading `'I/O problem' (-7)`.
   `fp-lapse.service` runs as root, so production is fine — but we log a
   clear root-required hint if we see that error so a future debugger isn't
   misled.

All exposed values are human-friendly (shutter in seconds, aperture as an
f-number, ISO as the speed value). The pure nearest-match translation to/from
gphoto2 choice labels lives in `nikon_values.py` (importable and tested on
the Mac without gphoto2).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

import gphoto2 as gp  # noqa: E402

from .iface import (  # noqa: E402
    ApertureParam,
    CameraBusy,
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
from .nikon_values import (  # noqa: E402
    aperture_to_label,
    iso_to_label,
    label_to_aperture,
    label_to_iso,
    label_to_seconds,
    seconds_to_label,
)

# gphoto2 widget names on the D5600 (PTP-driver config tree).
_W_SHUTTER = "shutterspeed"
_W_APERTURE = "f-number"
_W_ISO = "iso"
_W_EXPPROGRAM = "expprogram"
_W_FOCUSMODE = "focusmode2"
_W_FOCUSMODE_FALLBACK = "focusmode"
_W_BATTERY = "batterylevel"
_W_CAPTURETARGET = "capturetarget"
_W_MANUFACTURER = "manufacturer"
_W_MODEL = "cameramodel"
_W_SERIAL = "serialnumber"
_W_FIRMWARE = "deviceversion"

# Dial-position labels (expprogram choices) that map to ExposureMode.
_DIAL_TO_EXPOSURE = {
    "M": ExposureMode.MANUAL,
    "Manual": ExposureMode.MANUAL,
    "A": ExposureMode.APERTURE_PRIORITY,
    "Aperture priority": ExposureMode.APERTURE_PRIORITY,
    "S": ExposureMode.SHUTTER_PRIORITY,
    "Shutter priority": ExposureMode.SHUTTER_PRIORITY,
    "P": ExposureMode.PROGRAM,
    "Program": ExposureMode.PROGRAM,
    "Auto": ExposureMode.PROGRAM,
}

# focusmode labels → FocusMode.
_FOCUS_FROM_LABEL = {
    "Manual": FocusMode.MF,
    "MF (fixed)": FocusMode.MF,
    "Manual focus": FocusMode.MF,
    "AF-S": FocusMode.AF_S,
    "AF-C": FocusMode.AF_C,
}

# gphoto2 GP_ERROR_IO is -7 ('I/O problem'); the spike showed this is what a
# permission failure (run-as-non-root) looks like from userspace.
_GP_ERROR_IO = getattr(gp, "GP_ERROR_IO", -7)

# IO/transport error codes that mean "the device is gone" → CameraNotConnected.
# Resolved by name via getattr/hasattr so a constant absent from the installed
# libgphoto2 binding is simply skipped — never an AttributeError raised INSIDE
# the `except gp.GPhoto2Error` handler (which would mask the real transport
# error and bypass _mark_disconnected, leaving the health thread unable to
# recover).
_IO_ERROR_CODES = frozenset(
    getattr(gp, _name) for _name in (
        "GP_ERROR_IO", "GP_ERROR_IO_READ", "GP_ERROR_IO_WRITE",
        "GP_ERROR_IO_USB_FIND", "GP_ERROR_IO_USB_CLAIM", "GP_ERROR_IO_LOCK",
    ) if hasattr(gp, _name)
)
_GP_ERROR_CAMERA_BUSY = getattr(gp, "GP_ERROR_CAMERA_BUSY", -110)


class NikonGPhotoCamera:
    """Real `Camera` adapter for the Nikon D5600 over gphoto2."""

    def __init__(self) -> None:
        self._cam: Optional["gp.Camera"] = None
        # Serialises all libgphoto2 operations (see module docstring).
        self._lock = threading.RLock()
        # Cached requested params for CaptureResult (the engine doesn't read
        # it; cached values avoid a status() roundtrip on the hot path).
        self._last_shutter_s: Optional[float] = None
        self._last_aperture: Optional[float] = None
        self._last_iso: Optional[int] = None
        # Cached focus mode, driven by set_params and refreshed in shoot().
        self._cached_focus_mode: Optional[FocusMode] = None
        # Set when the engine's requested exposure mode disagrees with the
        # physical dial position; surfaced via the `dial_mismatch` property for
        # the UI ("DIAL NOT ON M").
        # INVARIANT: refreshed only inside set_params(exposure_mode=...). The
        # engine passes exposure_mode on EVERY fire (engine._apply_shot_params),
        # so the flag stays current frame-to-frame. A caller that invokes
        # set_params WITHOUT exposure_mode does NOT refresh it (it would read
        # stale until the next exposure_mode call) — preserve that contract if
        # the engine's per-fire behaviour ever changes.
        self._dial_mismatch: bool = False

    # --- lifecycle ---
    def _mark_disconnected(self, exc: Exception) -> CameraNotConnected:
        """Drop the gphoto2 handle after a transport error.

        Logs a root-required hint if the error looks like a permission
        failure (GP_ERROR_IO), which on the Pi means "not running as root".
        """
        if _looks_like_io_permission_error(exc):
            logger.error(
                "nikon_gphoto: gphoto2 I/O problem (-7). On the Pi this is "
                "almost always a permissions issue — gphoto2 must run as "
                "ROOT (libusb_open fails 'Access denied' as user pi). "
                "fp-lapse.service runs as root; check the unit if you see "
                "this. Original error: %s", exc,
            )
        self._cam = None
        self._cached_focus_mode = None
        return CameraNotConnected(f"transport error: {exc}")

    def _classify(self, exc: Exception):
        """Map a gphoto2 error to the right `Camera` exception.

        Transport / device-gone / I/O → CameraNotConnected (via
        _mark_disconnected). Busy → CameraBusy. Everything else is left to
        the caller (typically wrapped as CaptureFailed in shoot()).
        """
        code = _gp_error_code(exc)
        if code is not None and code in _IO_ERROR_CODES:
            return self._mark_disconnected(exc)
        if code is not None and code == _GP_ERROR_CAMERA_BUSY:
            return CameraBusy(str(exc))
        return None

    def connect(self) -> None:
        with self._lock:
            if self._cam is not None:
                return
            self._connect_locked()

    def _connect_locked(self) -> None:
        cam = gp.Camera()
        try:
            cam.init()
        except gp.GPhoto2Error as e:
            raise self._mark_disconnected(e)
        self._cam = cam
        # Force shots onto the SD card (trigger-only; never downloaded to the
        # Pi), mirroring the Sigma's DestToSave=InCamera.
        try:
            config = cam.get_config()
            self._set_choice(config, _W_CAPTURETARGET, "Memory card")
            cam.set_config(config)
            logger.info("nikon_gphoto: set capturetarget=Memory card on connect")
        except gp.GPhoto2Error as e:
            logger.warning(
                "nikon_gphoto: could not set capturetarget=card: %s — shots "
                "may try to download to the Pi", e,
            )

    def disconnect(self) -> None:
        with self._lock:
            if self._cam is None:
                return
            try:
                self._cam.exit()
            except Exception:
                pass
            self._cam = None
            self._cached_focus_mode = None

    def is_connected(self) -> bool:
        return self._cam is not None

    def _require(self) -> "gp.Camera":
        if self._cam is None:
            raise CameraNotConnected("NikonGPhotoCamera is not connected")
        return self._cam

    # --- liveness ---
    def probe(self) -> None:
        """Liveness round-trip for the health watchdog (see Camera.probe).

        Uses `get_summary()` deliberately: on the D5600, after a USB unplug
        libgphoto2 keeps returning a CACHED config indefinitely — verified
        on hardware 2026-05-27, `get_config()` stayed OK while
        `get_summary()`/`get_storageinfo()` flipped to GP_ERROR_IO (-52).
        So `get_config()` is NOT a reliable disconnect probe; `get_summary()`
        does a real PTP round-trip and raises when the body is gone, letting
        `camera_health` detect the drop and hot-swap. Before this was an
        explicit probe(), the health thread called info() (which used
        get_config) and a Nikon unplug went unnoticed until the next shoot().
        """
        with self._lock:
            cam = self._require()
            try:
                cam.get_summary()  # real PTP round-trip; raises GP_ERROR_IO (-52) on unplug
            except gp.GPhoto2Error as e:
                raise self._mark_disconnected(e)

    # --- config-widget helpers ---
    @staticmethod
    def _get_widget(config, name):
        """Return the named child widget, or None if absent."""
        try:
            return config.get_child_by_name(name)
        except gp.GPhoto2Error:
            return None

    @staticmethod
    def _get_value(config, name):
        w = NikonGPhotoCamera._get_widget(config, name)
        if w is None:
            return None
        try:
            return w.get_value()
        except gp.GPhoto2Error:
            return None

    @staticmethod
    def _get_choices(config, name) -> List[str]:
        w = NikonGPhotoCamera._get_widget(config, name)
        if w is None:
            return []
        try:
            return [w.get_choice(i) for i in range(w.count_choices())]
        except gp.GPhoto2Error:
            return []

    @staticmethod
    def _set_choice(config, name, label: Optional[str]) -> bool:
        """Set the named widget to `label`. Returns True if written.

        Never used for the read-only `expprogram` widget.
        """
        if label is None:
            return False
        w = NikonGPhotoCamera._get_widget(config, name)
        if w is None:
            return False
        w.set_value(label)
        return True

    # --- introspection ---
    def info(self) -> CameraInfo:
        with self._lock:
            cam = self._require()
            try:
                # Identity read only. Liveness is NOT info()'s job — the
                # health watchdog calls probe() (a real PTP round-trip) for
                # that. get_config() here is fine even though it can return a
                # cached config after an unplug: detecting the disconnect is
                # probe()'s responsibility, not info()'s.
                config = cam.get_config()
            except gp.GPhoto2Error as e:
                raise self._mark_disconnected(e)
            manuf = self._get_value(config, _W_MANUFACTURER) or "Nikon"
            model = self._get_value(config, _W_MODEL) or "D5600"
            return CameraInfo(
                model=str(model),
                firmware=str(self._get_value(config, _W_FIRMWARE) or ""),
                serial=str(self._get_value(config, _W_SERIAL) or ""),
            )

    def status(self) -> CameraStatus:
        with self._lock:
            cam = self._require()
            try:
                config = cam.get_config()
            except gp.GPhoto2Error as e:
                raise self._mark_disconnected(e)

            shutter_label = self._get_value(config, _W_SHUTTER)
            aperture_label = self._get_value(config, _W_APERTURE)
            iso_label = self._get_value(config, _W_ISO)
            shutter_s = label_to_seconds(shutter_label) if shutter_label else None
            aperture = label_to_aperture(aperture_label) if aperture_label else None
            iso = label_to_iso(iso_label) if iso_label else None
            iso_auto = bool(iso_label and str(iso_label).strip().lower() == "auto")

            dial_label = self._get_value(config, _W_EXPPROGRAM)
            exposure_mode = _DIAL_TO_EXPOSURE.get(str(dial_label)) if dial_label else None

            focus_mode = self._read_focus_mode(config)

            battery_pct = _parse_battery(self._get_value(config, _W_BATTERY))

            return CameraStatus(
                shutter_s=shutter_s,
                aperture=aperture,
                iso=iso,
                iso_auto=iso_auto,
                exposure_mode=exposure_mode,
                focus_mode=focus_mode,
                battery_pct=battery_pct,
                sd_free_bytes=None,
            )

    def _read_focus_mode(self, config) -> Optional[FocusMode]:
        label = self._get_value(config, _W_FOCUSMODE)
        if label is None:
            label = self._get_value(config, _W_FOCUSMODE_FALLBACK)
        if label is None:
            return None
        return _FOCUS_FROM_LABEL.get(str(label))

    @property
    def dial_mismatch(self) -> bool:
        """Whether the last `set_params` saw the dial in the wrong mode."""
        return self._dial_mismatch

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
        with self._lock:
            cam = self._require()
            try:
                config = cam.get_config()
            except gp.GPhoto2Error as e:
                raise self._mark_disconnected(e)

            # Exposure mode is READ-ONLY on the D5600 dial. Never write it.
            # Read the dial; on a mismatch with the requested mode, warn and
            # record it for status()/the UI — but never raise.
            if exposure_mode is not None:
                dial_label = self._get_value(config, _W_EXPPROGRAM)
                dial_mode = _DIAL_TO_EXPOSURE.get(str(dial_label)) if dial_label else None
                mismatch = dial_mode is not None and dial_mode != exposure_mode
                self._dial_mismatch = mismatch
                if mismatch:
                    logger.warning(
                        "nikon_gphoto: engine wants exposure_mode=%s but the "
                        "dial is on %r (%s). The D5600 dial is read-only over "
                        "USB — shots will use the dial mode. Set the dial to "
                        "match for deterministic exposure.",
                        exposure_mode.name, str(dial_label),
                        dial_mode.name if dial_mode else "unknown",
                    )

            wrote = False
            try:
                if shutter_s is not None:
                    label = seconds_to_label(
                        shutter_s, self._get_choices(config, _W_SHUTTER))
                    if self._set_choice(config, _W_SHUTTER, label):
                        wrote = True
                    self._last_shutter_s = shutter_s
                if aperture is not None:
                    label = aperture_to_label(
                        aperture, self._get_choices(config, _W_APERTURE))
                    if self._set_choice(config, _W_APERTURE, label):
                        wrote = True
                    self._last_aperture = aperture
                if iso is not None:
                    label = iso_to_label(iso, self._get_choices(config, _W_ISO))
                    if self._set_choice(config, _W_ISO, label):
                        wrote = True
                    self._last_iso = iso
                if focus_mode is not None:
                    # Best-effort: setting AF mode over USB is unreliable on
                    # entry-level Nikons; never let it abort the call.
                    self._cached_focus_mode = focus_mode
                    try:
                        label = _focus_to_label(focus_mode,
                                                self._get_choices(config, _W_FOCUSMODE))
                        if label and self._set_choice(config, _W_FOCUSMODE, label):
                            wrote = True
                    except gp.GPhoto2Error as fe:
                        logger.warning(
                            "nikon_gphoto: focus-mode write failed (ignored): %s", fe)
                if wrote:
                    cam.set_config(config)
            except gp.GPhoto2Error as e:
                mapped = self._classify(e)
                if mapped is not None:
                    raise mapped
                # Setting a parameter the body rejected — log, don't abort the
                # whole run (the engine treats raises as shot failures).
                logger.warning("nikon_gphoto: set_params write error (ignored): %s", e)

    # --- capture ---
    def shoot(self, timeout_s: float = 10.0) -> CaptureResult:
        with self._lock:
            return self._shoot_locked(timeout_s)

    def _shoot_locked(self, timeout_s: float) -> CaptureResult:
        cam = self._require()

        # Re-read focus mode each shoot (the user may flip the AF/MF switch
        # without going through set_params). With MF we take the non-AF path
        # so AF doesn't hunt between frames.
        try:
            config = cam.get_config()
            focus_mode = self._read_focus_mode(config)
        except gp.GPhoto2Error as e:
            raise self._mark_disconnected(e)
        if focus_mode is not None:
            self._cached_focus_mode = focus_mode
        effective_focus = self._cached_focus_mode

        if effective_focus == FocusMode.MF:
            # Non-AF capture path: trigger directly without driving AF.
            logger.debug("nikon_gphoto: MF → non-AF trigger capture")
            self._maybe_disable_autofocus(cam, config)

        t0 = time.monotonic()
        try:
            # Trigger-only: fire the shutter, let the image save to the SD
            # card. NOT capture_image_and_download — we never pull the file
            # to the Pi (1 GB RAM; a 26 MB RAW per frame is slow & pointless).
            cam.trigger_capture()
        except gp.GPhoto2Error as e:
            mapped = self._classify(e)
            if mapped is not None:
                raise mapped
            raise CaptureFailed(f"trigger_capture failed: {e}")

        duration_s = time.monotonic() - t0
        return CaptureResult(
            shutter_s=self._last_shutter_s or 0.0,
            aperture=self._last_aperture or 0.0,
            iso=self._last_iso or 0,
            duration_s=duration_s,
        )

    def _maybe_disable_autofocus(self, cam, config) -> None:
        """Best-effort: disable AF-on-trigger for the non-AF (MF) path.

        On the D5600 the safest MF behaviour is to leave the lens AF/MF
        switch on M; if a `viewfinder`/`autofocusdrive` toggle exists we
        avoid driving it. Failures here are non-fatal — MF shooting still
        works with the lens switch on M.
        """
        try:
            w = self._get_widget(config, "autofocusdrive")
            if w is not None:
                w.set_value(0)
                cam.set_config(config)
        except gp.GPhoto2Error:
            pass


def _focus_to_label(focus_mode: FocusMode, choices: List[str]) -> Optional[str]:
    """Map a `FocusMode` to the nearest available focusmode choice label."""
    wanted = {v: k for k, v in _FOCUS_FROM_LABEL.items()}.get(focus_mode)
    if wanted and wanted in choices:
        return wanted
    # Fall back to any choice that maps back to the requested mode.
    for label in choices:
        if _FOCUS_FROM_LABEL.get(label) == focus_mode:
            return label
    return None


def _gp_error_code(exc: Exception) -> Optional[int]:
    """Best-effort extraction of the numeric gphoto2 error code."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    # gphoto2.GPhoto2Error stores the code as the first arg in some versions.
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _looks_like_io_permission_error(exc: Exception) -> bool:
    code = _gp_error_code(exc)
    if code is not None and code == _GP_ERROR_IO:
        return True
    return "i/o problem" in str(exc).lower()


def _parse_battery(value) -> Optional[int]:
    """Parse the `batterylevel` widget value (e.g. "75%", "50%") → int pct."""
    if value is None:
        return None
    s = str(value).strip().rstrip("%").strip()
    try:
        return int(round(float(s)))
    except (ValueError, TypeError):
        return None
