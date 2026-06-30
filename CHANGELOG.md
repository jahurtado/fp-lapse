# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] — 2026-06-30

Adds **manual Wi-Fi configuration from the device itself** — no SSH, no
external keyboard. In the field (a fresh shooting location, eclipse day
in Benavente) the operator can now point the box at a new network from
the TFT and the 6 buttons alone, so the schedule layer's trusted clock
can anchor from NTP. This ships the project's **first on-screen virtual
keyboard**, the sub-screen `docs/reference.md §7.6` had deferred until a
real case justified it.

### Added

- **SETTINGS menu (LEFT on the main screen).** The former TIME SETUP
  modal becomes a flat, single-level `SETTINGS` menu — `Sync Time (NTP)`
  · `Set Time (Manual)` · `Wi-Fi setup`. The first two are the existing
  clock items, relabelled so they read as device-clock actions; their
  behaviour is unchanged.
- **Wi-Fi setup flow.** Scan nearby networks (cached on entry, with an
  explicit `Rescan`), pick one — signal-strength glyph, lock marker for
  secured, a green `●` on the active network, plus `Other network…` for
  a hidden/typed SSID — then connect off the UI thread with a
  `Connecting…` animation and a 30 s timeout, ending on a clear
  `Connected` (with the obtained IP) or failure screen. Backed by a
  thin, import-safe `nmcli` wrapper (`src/fp_lapse/net/nmcli.py`,
  modelled on `shutdown.py`); runs as root (the service already does)
  and stays mock-driven on the Mac dev harness.
- **On-screen virtual keyboard (`src/fp_lapse/ui/keyboard.py`).** An
  alphabetical grid with a layer key cycling `abc → ABC → 123 → #+=`
  (full printable-ASCII coverage), `␣` / `⌫` / `✓ Done`, and a masked
  password with a show/hide toggle. Built generically (carries a
  `target` discriminator) but wired only to the Wi-Fi SSID/password
  fields for now.
- **Per-network gestures on the list.** Short **OK** connects — open or
  already-saved networks join with stored credentials (no keyboard);
  a secured network with no saved profile opens the keyboard. **Hold
  OK** edits/replaces the password. **Hold ESC** forgets a saved
  network (behind a `Forget?` confirmation). The OK+ESC safe-shutdown
  chord still supersedes either single-button long-press.

### Changed

- Main-screen footer hint `← time setup` → `← settings`.
- `ButtonRouter` now arms a long-press timer for **both** OK and ESC
  (was OK-only), so the list can distinguish short (connect / back) from
  long (edit / forget) presses; the shutdown chord keeps priority.

### Fixed

- After a successful connect, the network list is re-scanned and the
  active `●` marker (and `saved` flag) reconciled against the live
  connection, so the dot follows the network you just joined instead of
  sticking to the one from the entry scan.

## [1.3.0] — 2026-06-02

Closes two silent data-loss footguns in the PTP capture path that a
night-long bracketed timelapse on 2026-06-01 surfaced — the Sigma fp
was clicking on every frame regardless of the `Sound → Shutter Sound`
menu, and silently saving JPEG even with the menu set to DNG — and
adds a hardware-button safe-shutdown path so the rig no longer needs
a powerbank pull to switch off (one of those killed the rootfs in the
same session).

### Added

- **Safe shutdown chord (§7.8 of `docs/reference.md`).** Hold OK + ESC
  together for 3 s from any screen → modal `Power off?` overlay →
  confirm with OK → full-screen `POWERING OFF…` message with a hint to
  wait for the green LED before unplugging. The pitft22 keeps that
  frame in panel memory across the kernel halt so the operator sees a
  clear "OK to disconnect" signal until the powerbank is pulled.
  Eliminates the filesystem-corruption risk of yanking the powerbank
  while writes are in flight.
- **Two-line footer on the main screen** so the global LEFT / RIGHT /
  chord shortcuts have permanent visibility without crowding the
  state-dependent primary line. Line 2 reads
  `← time setup  → sched on/off  OK+ESC shutdown` from every state.

### Changed

- **Schedule indicator semantics.** The colored dot now always
  reflects the would-be engine state (red / green / yellow based on
  trusted-clock health) regardless of whether the schedule global is
  enabled. When the schedule is disabled, the clock pictogram is
  drawn with a diagonal strikethrough — the operator sees both pieces
  of information at once (would the engine be firing? + is the
  schedule armed right now?) instead of the previous "vanish on
  disable" behaviour that hid the underlying state.
- **`BACK` button label renamed to `ESC` throughout the UI** (footer
  hints, confirmation overlays, the datetime picker) to match the
  silkscreen on the physical button. Internal identifiers
  (`ButtonId.BACK`) stay the same — the rename is purely operator-
  facing.
- **Version stamp moved from the footer to the top-right of the
  status bar** (DIM colour). Frees the footer line for hint text and
  groups the live metadata (clock, camera model, SKIPS, schedule
  indicator, version) in one place.
- **Save-overlay past-date warning shortened** from `"Note: start
  date is in the past — won't fire."` to `"Start date past — won't
  fire"` so it fits inside the 240 px-wide modal dialog at mono-11
  (the previous text overflowed both sides).

### Fixed

- **Sigma fp PTP capture no longer plays the simulated shutter
  click** regardless of menu setting. The Sigma firmware's
  `config_api()` call (issued by every PTP session) resets camera
  settings to PTP-session defaults, so the user's
  `Sound → Shutter Sound = 0` setting was being silently reverted on
  every connect. The adapter now pushes
  `CamDataGroup4(ShutterSound=0)` explicitly after `config_api()`.
- **Sigma fp PTP capture no longer saves as JPEG when the camera
  menu says DNG.** Same root cause as the shutter sound fix:
  `config_api()` resets `ImageQuality` to its session default. The
  adapter now pushes `CamDataGroup2(ImageQuality=DNG)` explicitly on
  connect. Without this fix, an entire bracketed timelapse silently
  saves as lossy 8-bit JPEG with white balance and tone baked in —
  observed in the field on the night of 2026-06-01, recoverable
  only via `enfuse` / Lightroom and never as good as raw.

### Notes

- The safe-shutdown chord is **global** — it fires from EDIT, MANAGE,
  PICKER and every overlay, not just MAIN. There is no progress
  indicator during the 3 s hold (a deliberate "the chord is the
  confirmation" choice); the modal that appears after the hold is the
  abort path. Releasing either button before the 3 s elapse cancels
  the chord silently.
- The two PTP fixes apply only to the Sigma fp adapter. The Nikon
  D5600 uses gphoto2 and a different settings model; image quality
  and sound there are honoured from the camera menu directly.

## [1.2.0] — 2026-05-30

Adds **scheduled start/end times per configuration** so the rig can run
unattended through a planned event (headline use case: the 2026-08-12
total solar eclipse, but equally good for daily sunrise / sunset
timelapses). A global `schedule` flag arms the system; once armed, the
engine starts and switches configurations at their scheduled instants
without any operator touch. Time anchoring is via `systemd-timesyncd` at
boot (no RTC needed) and includes a sanity envelope so a glitched NTP
response can't fire spurious events.

### Added

- **Per-configuration `start` and `end` moments**. Each schedulable
  moment is either an absolute datetime (`YYYY-MM-DD HH:MM:SS`, fires
  once and never again), a time-of-day only (fires every day at that
  time — useful for daily sunrise / sunset routines), or empty (no
  schedule). Edited via two new fields in the per-config editor.
- **Schedule global on/off**, toggled with the RIGHT button on the main
  screen and persisted to `runtime/schedule_state.json` across reboots.
- **Schedule indicator** in the top-right of the status bar: a small
  clock pictogram + colored dot. **Red** = armed but no NTP sync yet
  (engine inert); **green** = armed and synced fresh (engine firing);
  **yellow** = armed and synced but stale (>2 h since last sync) or the
  last sync was rejected by the trusted-clock envelope (engine still
  firing on its last good anchor). Nothing renders when the schedule is
  off.
- **`TIME SETUP` menu** opened with a short press of the LEFT button on
  the main screen. Two options: `Force NTP sync` (kicks an immediate
  re-sync via `systemd-timesyncd` and unconditionally trusts the result;
  shows an animated `Syncing.` / `..` / `...` while it runs) and
  `Set manually` (opens the datetime picker in system-clock mode to
  enter the time by hand when no network is available).
- **Datetime picker overlay** with a leftmost **mode chip** that cycles
  `[—]` (clear) / `[TIME]` (time-only, daily) / `[DATE+TIME]` (one-shot)
  using UP/DOWN, and per-digit editing for the value. Reachable from the
  editor by pressing LEFT or RIGHT on a START / END field.
- **Schedule lines** under each config name on the main screen — shows
  the upcoming start (`▶`) and/or end (`■`) with their times. One-shot
  pairs that share the same date collapse to a single line for
  compactness (`2026-08-12  ▶ 11:33:23  ■ 11:36:09`).
- **Auto-scroll on the main screen** when the list of configurations no
  longer fits in the visible area. The block under the cursor stays
  visible; moving UP / DOWN scrolls the list automatically.
- **Field-setup section in the README** with `nmcli` recipes for
  registering a phone hotspot as a fallback Wi-Fi network, so the Pi
  can get its boot-time NTP sync away from the home network.

### Changed

- **`runtime/configs.json` schema bumped from v1 to v2** to carry the
  new `start` / `end` fields. Existing v1 files load unchanged
  (with both fields defaulting to `None`); the file is rewritten as v2
  on the next save. No manual migration step required.
- **Overlay transitions are now fully opaque** (datetime picker, TIME
  SETUP menu, manage menu, confirmation dialogs). The previous
  semi-transparent dim-through-to-previous-screen pattern was hard to
  read on the small TFT and was replaced with a clean opaque background.
- **Wall-clock display in the status bar reads from the trusted clock**
  instead of `datetime.now()` whenever a baseline is available. This
  means the visible time on screen is always the time the engine is
  actually using for scheduling — a rogue NTP response never makes the
  TFT show a wildly different time from what the engine is firing on.
- **OK in the per-config editor uniformly saves**, on every field. The
  previous behaviour where OK on START / END opened the picker (rather
  than save) is gone — the picker is now reached with LEFT or RIGHT on
  those fields, and OK has a single consistent job everywhere.
- **Footer hint on the main screen** updates dynamically: idle row on a
  real config shows `↑↓ nav  OK run  hold OK menu`; the `+ New` and
  running-on-running rows show `↑↓ nav  …  ← time → sched` to advertise
  the new schedule affordances.


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
