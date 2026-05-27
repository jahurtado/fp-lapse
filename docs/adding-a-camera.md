# Adding support for a new camera — Claude Code playbook

This is an **executable playbook for Claude Code**. When the user says something
like *"port fp-lapse to the Sony A7 III"* or *"add support for my Canon EOS R"*,
read this file and follow it. The goal: build the new adapter **as autonomously
as possible**, front-loading every question and physical action so the session
doesn't stall on round-trips.

It is the generalised, hard-won version of two real ports already in the tree:
the **Sigma fp** (`sigma-ptpy`) and the **Nikon D5600** (`gphoto2`). The Nikon
port is the canonical worked example — copy it.

> **Reference implementation to read first:**
> `src/fp_lapse/camera/nikon_gphoto.py`, `detect.py`, `proxy.py`, `iface.py`
> and `nikon_values.py`. The Nikon adapter is the canonical worked example;
> this playbook assumes that architecture.

---

## 0. The shape of the work (so you can plan)

The camera layer is behind a `Camera` Protocol (`src/fp_lapse/camera/iface.py`);
the engine, UI and health watchdog depend only on it. Adding a camera is
**additive** — a new adapter + a detection entry + a proxy/factory branch + a
deps extra. The engine, `configs.py`, `MockCamera` and the Protocol shape do
**not** change.

Two transports exist:
- **`sigma-ptpy`** — Sigma fp only (vendor PTP). You will almost never use this
  for a new body.
- **`gphoto2` / `libgphoto2`** — everything else. **This is the path for a new
  Sony/Canon/Nikon/Fuji/… body.** `nikon_gphoto.py` is your template.

Because a new body makes **two** gphoto2 adapters, the "extract a shared base"
threshold (3+ near-duplicates) is finally met — see §6.

---

## 1. Feasibility check (autonomous, ~5 min)

Before anything, confirm the camera is drivable:

1. **Is it in libgphoto2?** Fetch its capabilities file:
   `https://github.com/gphoto/libgphoto2/blob/master/camlibs/ptp2/cameras/<model-slug>.txt`
   (or `ssh pi3 'gphoto2 --list-cameras | grep -i <brand>'`). Confirm it lists
   **image capture** and **config support**.
2. **Can you set shutter / ISO / aperture remotely?** Look for `shutterspeed`,
   `iso`, `f-number` (or `aperture`) as **read-write** config.
3. **Is the exposure mode a settable property or a physical dial?** This is the
   single most important behavioural fork (see §5). On the Nikon D5600 the dial
   (`expprogram`) is **read-only over USB**; on some Sony/Canon bodies the mode
   is settable. Decide which your target is.

If the camera is not gphoto2-supported, stop and tell the user — the project
constraints forbid proprietary SDKs and x86-only blobs.

---

## 2. Front-load everything you need from the user (ONE batch)

The most expensive thing in a camera port is **stalling on the human**. Ask all
of this up front, in one message, *before* touching the Pi:

**Physical setup (ask the user to do this once and leave it):**
- Connect the camera to the **Pi** by USB, **powered on**, with a **charged
  battery** and an **SD card** inserted.
- Set the camera's **USB mode to remote/PTP camera-control** (NOT mass storage /
  MTP-file-transfer). Brand-specific: Sony → *PC Remote*; Canon → default PTP;
  Nikon → default; Sigma fp → *USB Mode → Camera Control*.
- Set the **mode dial to M** (manual) and the **lens AF/MF switch to MF** (MF is
  the reliable timelapse setting — AF must not hunt between frames).
- **Leave it connected and powered for the whole session.** You'll ask them to
  unplug only for the disconnect test (§5, step E4) — and that you coordinate
  with a background poll, never a timed countdown.

**Decisions (ask once):**
- If **both** the new camera and an existing one might be attached at once,
  what's the priority? (Current default: Nikon wins; document and make it
  overridable via `FP_LAPSE_CAMERA`.)
- Is the exposure mode settable over USB on this body, or a read-only dial?

Then verify autonomously: `ssh pi3 'lsusb'` and `ssh pi3 'gphoto2 --auto-detect'`
— capture the **VID:PID** (you need it for `detect.py`).

---

## 3. Smoke test FIRST (autonomous, on the Pi, ~10 min)

Never implement before proving the camera shoots **and honours exposure** over
USB. This is a standalone CLI test — no project code.

```bash
# gphoto2 must be installed (apt — ask the user to approve once):
ssh pi3 'which gphoto2 || sudo apt-get install -y gphoto2'

# Only ONE process may own the camera USB. Stop the service (ask once):
ssh pi3 'sudo systemctl stop fp-lapse'

# Detect, then VERIFY PARAMETER CONTROL (not just "a photo came out"):
ssh pi3 'gphoto2 --auto-detect'
ssh pi3 'sudo gphoto2 --set-config iso=800 --set-config shutterspeed=0.0020s \
         --capture-image-and-download --filename /tmp/smoke-%C'
# Pull the JPG and read EXIF (PIL is in the venv, or exiftool):
#   confirm ISO == 800 and exposure == 1/500.
```

**Hard lesson (cost hours):** a smoke test that only captures a frame with the
camera's current settings is *not enough*. The intervalometer's whole job is
setting exposure — **you must confirm via EXIF that the requested ISO/shutter
landed.** A frame coming out tells you nothing.

Capture: the exact **gphoto2 widget names and choice-label formats** for this
body (`gphoto2 --get-config iso`, `--get-config shutterspeed`, etc.). They vary
by brand (e.g. shutter labels `"0.0020s"` vs `"1/500"`; aperture `"f/5.6"` vs
`"5.6"`). You'll hard-code/translate these in the adapter.

Restart the service when done (`sudo systemctl start fp-lapse`).

---

## 4. Pitfalls — read these BEFORE coding (each one cost real time)

- **Run gphoto2 as ROOT.** As user `pi`, `libusb_open` fails `Access denied
  (-3)`, which gphoto2 surfaces as a misleading `'I/O problem' (-7)`.
  `fp-lapse.service` runs as root, so production is fine — but standalone tests
  need `sudo`, and the adapter should log a root hint on that error.
- **`probe()` MUST do a real round-trip — NOT `get_config()`.** gphoto2 returns
  a **cached** config after a USB unplug; `get_config()` keeps returning `OK`
  forever while the camera is gone, so a disconnect goes undetected. Use
  `get_summary()` (proven on the D5600: flips to `GP_ERROR_IO` on unplug). This
  was a real shipped-then-caught bug. See `iface.Camera.probe`.
- **Pick the adapter by VID/PID BEFORE opening the bus.** `sigma-ptpy`/`ptpy`
  selects by PTP *class*, so it would seize any PTP body. Detection
  (`detect.py`) reads **descriptors only**, never opens a session.
- **Only ONE process owns the camera USB.** Stop `fp-lapse.service` for any
  standalone gphoto2 test, or `fp-lapse` (pyusb) will hold the device.
- **`gphoto2` and `pyusb` are NOT installed on the Mac.** Keep all pure logic
  (value translation, detection) in modules that don't import them, so they're
  unit-tested on the Mac. The adapter imports `gphoto2` at module top → it is
  **not importable on the Mac**, so its tests are **source-level (AST/string)
  checks**, like `test_nikon_gphoto.py`/`test_sigma_fp_dispatch.py`. Behavioural
  proof is on the Pi (`validation.md`).
- **The Pi runs Python 3.13** (Trixie); piwheels has **no cp314 armv7 wheels**.
  `.python-version` is pinned to `3.13`. The service runs `.venv/bin/python`
  directly. Never bump the pin past what piwheels builds for armv7.
- **Exposure-dial read-only vs settable.** If read-only (D5600): `set_params`
  must NOT write it and must NOT raise — read it, and on a mismatch with the
  engine's requested mode, log a warning and record a `dial_mismatch` flag for
  the UI (`DIAL NOT ON M`). If settable: set it like any other param.
- **Camera USB session is stateful.** Open the handle once at `connect()`, keep
  it for the whole run, never per shot. Killing a process that holds the device
  can leave it needing a re-enumerate (sysfs `authorized` toggle or a replug).

---

## 5. The build (TDD) — step by step

**Approach: TDD.** Spec the change, implement test-first (red → green →
refactor), then review the diff and fix what it surfaces. Keep the full suite
green throughout. Use whatever workflow/tooling you have — the steps below are
tooling-agnostic.

**A. Spec.** Write a short spec/notes for the port capturing the smoke-test
facts (VID/PID, widget names, dial settable?, label formats) and the decisions
you made — your single source of truth for the implementation step.

**B. Implement (test-first)**, mirroring the Nikon port:
- `src/fp_lapse/camera/<brand>_gphoto.py` — the adapter. **Copy
  `nikon_gphoto.py`** and adjust: widget-name constants, the dial/focus label
  maps, and `set_params`' exposure-mode handling (settable vs read-only). Keep:
  single `RLock`, persistent handle, `_mark_disconnected`, `capturetarget=card`,
  **trigger-only** `shoot()` (save to SD, never download to the 1 GB Pi),
  `probe()` via `get_summary()`, error mapping via a `getattr`-built
  `_IO_ERROR_CODES` set, `battery_pct` from `batterylevel`.
- `src/fp_lapse/camera/<brand>_values.py` — value translation if the label
  formats differ from `nikon_values.py`; otherwise reuse/generalise it.
- `detect.py` — add the brand's VID constant and a branch in
  `select_camera_kind` (+ tests with fake device lists).
- `proxy.py` `_default_factory` — add a `kind` → adapter branch (lazy import).
- `__main__.py` — `FP_LAPSE_CAMERA` already accepts the kind via detect; no
  change beyond the new kind string.
- `pyproject.toml` — the `[nikon]` extra already pulls `gphoto2`; rename it to a
  shared name (e.g. `[gphoto]`) or add the new camera to it.
- UI — extend the model-label mapping in `proxy._short_model_label` (and the
  status-bar test PNGs only if the rendering changes).
- **Tests:** pure logic (translation/detection) behavioural on the Mac; adapter
  source-level. Keep the full suite green.

**C. Consider extracting a shared gphoto2 base.** With two gphoto2 adapters you
now have real (3+) duplication of `connect/disconnect/is_connected/_require/
_mark_disconnected/probe/info/status/_set_choice/_get_*`. Extract a
`GPhotoCamera` base (handle lifecycle + the generic widget plumbing + `probe()`)
and let `nikon_gphoto`/`<brand>_gphoto` subclass it, overriding only the
widget-name maps and the dial policy. The review of the Nikon port explicitly
flagged this as "revisit when a third adapter lands" — this is that moment.

**D. Deploy + hardware-validate** (the proof; mostly autonomous):
1. `rsync -av --delete --exclude '.git' --exclude '.venv' --exclude '__pycache__'
   --exclude 'runtime' ./ pi3:~/fp-lapse/`
2. `ssh pi3 'cd ~/fp-lapse && uv sync --extra pi --extra <extra>'` (ask once for
   `libgphoto2-dev` via apt if `python-gphoto2` must build). Use
   `--python /usr/bin/python3.13` if uv tries the wrong interpreter.
3. Restart the service; confirm detection in the log
   (`camera detect: … → <kind>`, `camera proxy: now hosting kind=<kind>`).
4. **Objective validation via the control server, not the TFT:**
   `ssh pi3 'curl -s http://127.0.0.1:9999/state'` → check `camera.connected` and
   `camera.live` (iso/shutter/etc.). Set a config with a distinct ISO/shutter,
   run it, and confirm the captured frame's **EXIF** matches.
5. **Disconnect detection — use a background poll, never a timed unplug.** Timed
   countdowns failed twice (async lag). Instead:
   ```bash
   # run in the background; user unplugs at their own pace, then you read the log
   ssh pi3 'for i in $(seq 1 60); do printf "%s " "$(date +%H:%M:%S)"; \
     curl -s http://127.0.0.1:9999/state | grep -o "\"connected\": [a-z]*"; \
     sleep 2; done'
   ```
   Confirm `connected` flips `true`→`false` within ~5 s (one health tick) of the
   unplug, and back to `true` on reconnect.
6. If a second body is available, test the **bidirectional hot-swap** (unplug
   one, plug the other; the label and adapter switch with no restart).

**E. Review & fix.** Review the diff for correctness and tech debt, and fix what
it surfaces, before considering the port done.

---

## 6. What to do autonomously vs ask the human

**Do autonomously:** feasibility research, all code + tests, detection, the
spec, diagnosis, deploy, and validation via `/state` and EXIF. Read the smoke
test's gphoto2 output to learn the body's real widget names/labels rather than
guessing.

**Ask the human (only these):**
- The one-time **physical setup** (§2) and the **single unplug** for the
  disconnect test (coordinated via background poll).
- **Approvals** required by `CLAUDE.md`: `apt install` (gphoto2 / libgphoto2-dev),
  stopping `fp-lapse.service`, and any `/boot/firmware` edit.
- The **both-cameras priority** and the **dial settable?** decision (§2).
- Surface (don't silently work around) any **hardware fault**: Pi offline,
  loose cable, wrong USB mode, dead battery, camera asleep.

---

## 7. Done criteria

- [ ] New adapter satisfies the full `Camera` Protocol **including `probe()`**.
- [ ] Detection picks the new kind by VID/PID; `FP_LAPSE_CAMERA` override works.
- [ ] Smoke test (and adapter) confirmed by **EXIF**: requested ISO + shutter
      land on the captured frame.
- [ ] `probe()` detects an unplug within ~5 s (verified via background `/state`
      poll), and the camera reconnects automatically.
- [ ] `battery_pct` reads; exposure-dial policy correct for this body.
- [ ] Full test suite green on the Mac; adapter source-level tests + Mac-runnable
      pure-logic tests added.
- [ ] `CLAUDE.md` (camera layer), `docs/reference.md` (multi-camera), and the
      feature's `implementation-notes.md` / `validation.md` updated.
- [ ] VID/PID confirmed from real `lsusb` (no placeholders left).

---

## 8. The blunt summary for Claude

Copy `nikon_gphoto.py`. Learn the body's widget names from a `sudo gphoto2
--get-config` on the Pi (run as root, service stopped). Translate values to its
choice-label format. Add the VID to `detect.py` and a branch to the proxy
factory. `probe()` = `get_summary()`, never `get_config()`. Validate on the Pi
with `/state` + EXIF + a background-poll unplug. Ask the human only for physical
setup, apt/service approvals, and the two small decisions. Everything else, do
yourself.
