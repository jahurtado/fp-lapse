#!/usr/bin/env python3
"""check_camera.py — manual validation of the Camera abstraction layer.

Runs against the REAL Sigma fp through `SigmaFpCamera`, the adapter that
implements the `Camera` Protocol from `fp_lapse.camera.iface`. Confirms that
the adapter exposes the same observable behaviour as `MockCamera` end to
end: connect, read info/status, set params, snap, restore.

Requires:
  - Camera in STILL mode, lens attached, SD card inserted
  - USB Mode: Camera Control

Usage on the Pi:
  sudo ~/fp-lapse/.venv/bin/python ~/fp-lapse/scripts/check_camera.py
  sudo ~/fp-lapse/.venv/bin/python ~/fp-lapse/scripts/check_camera.py \\
      --shutter 1.0 --aperture 3.5 --iso 100
  sudo ~/fp-lapse/.venv/bin/python ~/fp-lapse/scripts/check_camera.py --no-snap
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from fp_lapse.camera import (  # noqa: E402
    CaptureFailed,
    ExposureMode,
    FocusMode,
)
from fp_lapse.camera.sigma_fp import SigmaFpCamera  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--shutter", type=float, default=1.0,
                   help="Shutter speed in seconds (default 1.0).")
    p.add_argument("--aperture", type=float, default=3.5,
                   help="Aperture f-number (default 3.5).")
    p.add_argument("--iso", type=int, default=100, help="ISO (default 100).")
    p.add_argument("--no-snap", action="store_true",
                   help="Skip the shutter trigger; only validate read/write.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cam = SigmaFpCamera()
    print("connect()...")
    cam.connect()
    try:
        print(f"info():   {cam.info()}")
        before = cam.status()
        print(f"status(): {before}")

        if args.no_snap:
            print("\n--no-snap: skipping set_params + shoot. Done.")
            return 0

        print(f"\nset_params(exposure_mode=MANUAL, focus_mode=AF_S, "
              f"shutter_s={args.shutter}, aperture={args.aperture}, "
              f"iso={args.iso})")
        cam.set_params(
            exposure_mode=ExposureMode.MANUAL,
            focus_mode=FocusMode.AF_S,
            shutter_s=args.shutter,
            aperture=args.aperture,
            iso=args.iso,
        )
        s_after_set = cam.status()
        print(f"status(): {s_after_set}")

        # Sanity: requested values should be reflected in status (within the
        # nearest encodable value — APEX converters round).
        for label, requested, got in (
            ("shutter_s", args.shutter, s_after_set.shutter_s),
            ("aperture",  args.aperture, s_after_set.aperture),
            ("iso",       args.iso, s_after_set.iso),
        ):
            if got is None or abs(got - requested) / max(requested, 1e-9) > 0.2:
                print(f"  WARNING: {label} requested={requested} got={got}")

        print("\nshoot()...")
        try:
            result = cam.shoot(timeout_s=15.0)
        except CaptureFailed as e:
            print(f"  FAILED: {e.reason}", file=sys.stderr)
            return 1
        print(f"  OK: {result}")

        print("\nrestoring original params (best-effort)...")
        cam.set_params(
            shutter_s=before.shutter_s,
            aperture=before.aperture,
            iso=before.iso,
            exposure_mode=before.exposure_mode,
            focus_mode=before.focus_mode,
        )
        return 0
    finally:
        print("\ndisconnect()...")
        cam.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
