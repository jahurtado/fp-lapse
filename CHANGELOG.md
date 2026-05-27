# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-05-27

Adds a second supported camera — the **Nikon D5600** — alongside the
Sigma fp, with automatic detection and runtime hot-swap.

### Added

- **Nikon D5600 support** over USB via `gphoto2` / `libgphoto2` (the
  `[nikon]` optional dependency, plus the `libgphoto2-dev` system package
  on the Pi). Manual exposure (ISO, shutter, aperture) is driven from the
  engine; frames are triggered to the camera's SD card, not downloaded to
  the Pi.
- **Automatic camera selection** by USB VID/PID at startup, and **runtime
  hot-swap** — unplug one body and plug in the other and the app
  reconfigures within a few seconds, no restart. Override the
  auto-detection with the `FP_LAPSE_CAMERA` environment variable
  (`mock` | `sigma_fp` | `nikon_d5600`).
- Status bar shows the live camera model (`fp` / `D5600`) and a
  `DIAL NOT ON M` warning when the Nikon's mode dial disagrees with the
  configured manual exposure.

### Changed

- The camera abstraction gained an explicit `probe()` liveness method;
  the camera-health watchdog uses it to detect silent USB disconnects
  reliably on both transports.

### Fixed

- Pinned the project to **Python 3.13** — the version Raspberry Pi OS
  Trixie ships and that piwheels builds armv7 wheels for.

### Notes

- The Nikon D5600's exposure-mode dial is read-only over USB: set it to
  **M** for deterministic manual exposure. gphoto2 must run as root (the
  systemd service already does).

## [1.0.0] — 2026-05-22

First public release. Functionally complete for the headline use
case (the 2026-08-12 total solar eclipse) and fully validated on a
Raspberry Pi 3 with a real Sigma fp connected over USB.

### Architecture

- Event-driven main loop. The engine runs in a dedicated scheduler
  thread that blocks on `time.sleep` until the next grid mark — grid
  precision is sub-millisecond on a Pi 3 regardless of render or GC
  load.
- Camera health thread that probes every 5 s and reconnects
  automatically on USB drop. Silent disconnects (cable yanked while
  the engine is idle) are detected within one tick.
- Single `app.lock` (RLock) serialises state between the scheduler,
  the GPIO/Tk callback threads, the long-press timer, and the UI
  thread.
- UI refresh is dirty-event driven with a 250 ms timeout — near-zero
  CPU when idle, instant response to events, "next in X.Xs" decimals
  keep updating.

### Engine

- Spec-compliant grid (`t0 + k·p`) on monotonic clock with
  injectable `now`.
- Hot config switch keeps `t0`; tolerances absorb 1-tick jitter.
- SKIP counter increments on missed instants (including brackets
  that fired but produced 0 images because the camera was
  disconnected mid-session).
- `ExposureMode` is set on every fire (Manual or Program depending
  on config) so moving the camera dial mid-session recovers
  automatically on the next shot.

### Data model

- `Shot.shutter: float` (seconds), `Shot.iso: int`, `Shot.aperture:
  Optional[float]`. Per-parameter `null` / `"auto"` sentinels are
  not in the schema.
- `TimelapseConfig.shots == []` is the legal **auto-mode** sentinel:
  one shot per interval, `ExposureMode=ProgramAuto`. Non-empty (1..9
  items) is manual mode.
- Atomic JSON persistence with rotating backup and corruption
  rescue. On boot, a corrupt `configs.json` is renamed aside and the
  app starts empty, with a `CONFIGS RESET` banner until the user
  saves something.

### Camera (Sigma fp via PTP)

- `sigma-ptpy` driven; `_compat.py` shims `collections.abc` for
  older `construct`.
- Adapter forces `ExposureMode=Manual` on connect and again per
  shoot.
- AF / MF dispatch: in MF the adapter sends `NonAFCapt` so the lens
  isn't asked to drive AF.
- 20 ms poll on `CaptStatus` after `SnapCommand`. CaptureResult
  populated from cached `set_params` values so no extra PTP
  roundtrips on the hot path.
- `USBError` and other transport faults are mapped to
  `CameraNotConnected`; the adapter atomically resets its session
  reference so the camera-health thread can reconnect.

### UI

- 320×240 RGB565 panel framebuffer on the Pi 3; RGB→RGB565
  packed with `numpy` (sub-millisecond). Pillow-based renderer; Tk
  mocks for Mac dev.
- Screens: MAIN, EDIT, MANAGE, plus four confirmation overlays —
  STOP (BACK while running), SAVE (OK in edit), DISCARD (BACK in
  edit with pending changes), DELETE (Manage → Delete).
- Persistent banners: `CAMERA NOT RESPONDING` after 5 consecutive
  failed shots (clears on success), `CONFIGS RESET` after a boot-
  time JSON rescue (clears on next save).
- Bottom-right version stamp (`v1.0.0`).
- Visual regression tested against committed PNG mockups.

### Tooling

- `Makefile` shortcuts: `test`, `run`, `deploy`, `ship`, `restart`,
  `logs`, `state`, `frame`, `e2e`, `shell`, `clean`. Variables
  override host/port. `make ship = deploy + restart + state`.
- `scripts/e2e_smoke.py`: end-to-end harness that drives the
  running service via the HTTP control surface, validates shots /
  skips, optionally reads TICK lateness from the journal.
- HTTP control server (`localhost:9999`, opt-in via
  `FP_LAPSE_CONTROL=1`): `GET /state`, `POST /press/{btn}` /
  `/release/{btn}` / `/tap/{btn}` / `/hold/{btn}/{ms}`,
  `GET /frame.png`. Used by `e2e_smoke.py` and ad-hoc testing.

### Deployment (Raspberry Pi 3 + pitft22 HAT)

- `fp-lapse.service` (systemd) launches `python -m fp_lapse` as
  root, `Restart=always` with burst limit, opens the control
  surface, runs at `multi-user.target` (no graphical environment).
- The Pi boots directly into the app on its TFT thanks to
  `fbcon=map:0` in `cmdline.txt` (documented in the README).
- Runs on Raspberry Pi OS Trixie (Python 3.13). The armv7l wheels for
  `numpy`, `Pillow` and `lgpio` come from piwheels.org so the Pi 3
  doesn't compile from sdist; `sigma-ptpy` installs from GitHub.

### Out of scope (deferred for future versions)

- No physical buzzer driver yet (the engine accepts an optional
  `BuzzerLike` if someone wires one later).
- No NTP / hotspot sync; the Pi boots with whatever clock it has
  and the engine uses monotonic time anyway.
- No internet access at runtime.
- No proprietary Sigma SDK.
- No event scheduling against wall-clock time.

[1.0.0]: https://github.com/jahurtado/fp-lapse/releases/tag/v1.0.0
