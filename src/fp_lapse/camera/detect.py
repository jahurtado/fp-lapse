"""Camera selection by USB VID/PID — pure decision + a thin USB wrapper.

Why this exists (see the PRD Problem Statement): both the Sigma fp and the
Nikon D5600 are PTP-class USB devices. `sigma-ptpy` (via `ptpy`) grabs a
camera **by PTP device class, not by VID/PID**, so it would happily seize
the Nikon and drive it with Sigma commands. Only one process can own the USB
camera at a time, so the adapter must be chosen **before any PTP library
opens the bus** — by reading USB descriptors only.

`select_camera_kind` is the single decision point used both at startup and
on every runtime hot-swap re-detection. It is **pure** (no `usb`, no adapter
imports) so the priority logic is unit-tested on the Mac with fake device
lists. The real USB enumeration (`enumerate_usb_ids`) imports `usb` lazily,
inside the function, so importing this module on a Mac without pyusb still
works.

Selection precedence (decisions for this implementation):

1. Manual override (`FP_LAPSE_CAMERA`, with `FP_LAPSE_MOCK=1` as a legacy
   alias for `mock`). Forces the kind unconditionally.
2. `is_darwin` → ``mock`` (Mac dev, no hardware).
3. Auto-detect by VID/PID. Nikon VID present → ``nikon_d5600``; Sigma
   VID/PID present → ``sigma_fp``. **When both are present, Nikon wins**
   (documented default; overridable via `FP_LAPSE_CAMERA`).
4. Fallback: nothing matched and not on Mac → ``sigma_fp`` (preserves the
   pre-feature single-camera behaviour), logged as a warning.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

UsbId = Tuple[int, int]  # (vid, pid)

KIND_MOCK = "mock"
KIND_SIGMA = "sigma_fp"
KIND_NIKON = "nikon_d5600"
_VALID_KINDS = (KIND_MOCK, KIND_SIGMA, KIND_NIKON)

# Nikon Corp. USB vendor id — confirmed for the D5600 (and Nikon DSLRs in
# general). Any Nikon body in PTP mode reports this VID; we don't pin the
# PID because we only support one Nikon model and detection is "a Nikon is
# attached" granular.
NIKON_VID = 0x04B0

# Sigma fp VID/PID. The Sigma was not attached during implementation, so
# these are placeholders used only for *positive* recognition when the real
# values are known. The no-match fallback (→ sigma_fp) covers the unknown
# case, so the system keeps working even if these are wrong.
# TODO: confirm via `lsusb` with the Sigma fp attached, then update.
SIGMA_FP_VID = 0x1003
SIGMA_FP_PID = 0xC432


def override_from_env(environ: Mapping[str, str]) -> Optional[str]:
    """Resolve the manual-override string from the environment.

    `FP_LAPSE_CAMERA` (case-insensitive) takes precedence; the legacy
    `FP_LAPSE_MOCK=1` is folded in as an alias for ``mock``. Returns the
    lowercased override or ``None`` when neither is set.
    """
    cam = environ.get("FP_LAPSE_CAMERA")
    if cam:
        return cam.strip().lower()
    if environ.get("FP_LAPSE_MOCK") == "1":
        return KIND_MOCK
    return None


def select_camera_kind(
    devices: Iterable[UsbId],
    *,
    override: Optional[str],
    is_darwin: bool,
) -> str:
    """Decide which camera adapter to build. Pure; see module docstring."""
    # Materialise once: the VID/PID scan below iterates `devices` twice, so a
    # one-shot generator would otherwise be exhausted on the second pass.
    devices = list(devices)
    if override in _VALID_KINDS:
        logger.info("camera detect: override forces kind=%s", override)
        return override  # type: ignore[return-value]
    if override:
        logger.warning(
            "camera detect: ignoring unknown FP_LAPSE_CAMERA=%r", override,
        )

    if is_darwin:
        return KIND_MOCK

    vids = {vid for (vid, _pid) in devices}
    pairs = set(devices)
    has_nikon = NIKON_VID in vids
    has_sigma = (SIGMA_FP_VID, SIGMA_FP_PID) in pairs

    if has_nikon and has_sigma:
        logger.info(
            "camera detect: both Nikon and Sigma attached — selecting Nikon "
            "(default priority; override with FP_LAPSE_CAMERA)",
        )
        return KIND_NIKON
    if has_nikon:
        logger.info("camera detect: Nikon (VID 0x%04x) → nikon_d5600", NIKON_VID)
        return KIND_NIKON
    if has_sigma:
        logger.info("camera detect: Sigma fp → sigma_fp")
        return KIND_SIGMA

    logger.warning(
        "camera detect: no supported camera VID/PID matched — falling back "
        "to sigma_fp (default). Attached: %s",
        sorted(f"0x{v:04x}:0x{p:04x}" for (v, p) in pairs) or "none",
    )
    return KIND_SIGMA


def enumerate_usb_ids() -> List[UsbId]:
    """Return ``(vid, pid)`` for every attached USB device.

    Imports `usb` (pyusb) lazily so this module is importable on a Mac with
    no pyusb. Reads descriptors only — never opens a PTP/usb session — to
    avoid seizing the camera before the right adapter is chosen. On any
    enumeration error returns an empty list (callers fall back gracefully).
    """
    try:
        import usb.core  # lazy: not available on a vanilla Mac
    except Exception as e:  # pragma: no cover - exercised only on the Pi
        logger.warning("camera detect: pyusb unavailable (%s)", e)
        return []
    try:
        out: List[UsbId] = []
        for dev in usb.core.find(find_all=True):
            try:
                out.append((int(dev.idVendor), int(dev.idProduct)))
            except Exception:
                continue
        return out
    except Exception as e:  # pragma: no cover - exercised only on the Pi
        logger.warning("camera detect: USB enumeration failed (%s)", e)
        return []
