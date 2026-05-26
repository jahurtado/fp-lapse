# Contributing to fp-lapse

Thanks for your interest in fp-lapse. This document covers the
development setup, the testing discipline, and the Mac↔Pi workflow used
to develop and deploy the project.

All code, comments, docstrings, and documentation in this repository are
written in **English** so anyone can read them without context
translation. Please keep contributions in English.

---

## Development model: develop on a computer, test on the Pi

Day-to-day development happens on a regular computer (the project was
built on a Mac), **not** on the Raspberry Pi:

- The whole UX — navigation, editing, the manage menu, the confirmation
  overlays — runs on the desktop through Tk-based mocks for the display,
  the buttons, and the camera. No hardware is required to work on the
  app logic or the UI.
- The Pi 3 holds the real hardware (TFT, buttons, Sigma fp). It runs a
  32-bit OS with 1 GB of RAM and is used for hardware validation only.
  All Pi-side work is done remotely over SSH.

---

## Setup

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management and execution.

```bash
# install uv (https://docs.astral.sh/uv/getting-started/installation/)
uv sync                # creates .venv/, installs locked deps
uv run fp-lapse        # opens the Tk window with the mock display
```

`FP_LAPSE_MOCK=1` forces mock mode even on Linux. Keyboard mapping for
the Tk mock buttons is documented in the README.

The base dependencies (Pillow, numpy) are platform-neutral. The
hardware-only dependencies live behind the `pi` extra:

```bash
uv sync --extra pi     # adds sigma-ptpy, gpiozero, lgpio (Pi only)
```

`sigma-ptpy` is not on PyPI; it is resolved from the
[`makanikai/sigma-ptpy`](https://github.com/makanikai/sigma-ptpy) GitHub
repo. It is needed only at install time — the runtime stays fully
offline.

---

## Tests

The suite is plain stdlib `unittest` and runs in a few seconds:

```bash
uv run python -m unittest discover -s tests
# or:
make test
```

Conventions:

- Every module under `src/fp_lapse/` has a `tests/test_X.py` companion.
- Each test file does `sys.path.insert(0, "src")` so it runs without an
  install step.
- Visual regression tests (`test_ui_*_screen.py`, `test_ui_overlays.py`,
  `test_ui_manage_menu.py`) compare rendered PIL bytes against the PNGs
  in `docs/mockups/`. When a render changes on purpose, regenerate the
  PNGs and commit the code and the new images together:

  ```bash
  uv run python docs/mockups/render_mockups.py
  ```

### Test-driven discipline

- **Bug fixes** ship with a regression test that fails without the fix
  and passes with it.
- **Behavior changes** are reflected in `docs/reference.md` (the
  functional source of truth) and pinned with a test before being
  merged.
- Don't merge code that lowers the test count without a clear reason.

---

## Code conventions

- Python ≥ 3.11.
- Code under `src/fp_lapse/`, tests under `tests/`.
- Layered, no circular dependencies: the hardware layer
  (`display` / `buttons`) has no dependencies; `camera`, `engine`, and
  `ui` sit above it; `configs` is a pure data model; `app.py`
  orchestrates. See the architecture diagram in the README.
- The engine runs in a dedicated scheduler thread that wakes on each
  grid mark; a camera-health thread reconnects the camera on USB drop.
  All shared state is guarded by a single `app.lock` (RLock). Keep new
  cross-thread state behind that lock.
- Logs go to a rotating file under `runtime/` plus stderr; every engine
  event and camera command is logged with a UTC timestamp.
- Docstrings cite the functional reference by section number
  (e.g. "§7.3 of docs/reference.md"). Keep those section numbers stable
  when editing the reference.

---

## Mac↔Pi deploy workflow

The included `Makefile` wraps the routine commands. Every target that
hits the Pi uses an SSH alias named `pi3` — set it up in your
`~/.ssh/config` to point at your own Pi (key-based, no password). You
can override the host per-invocation: `PI=otherhost make ship`.

```bash
make             # list all targets
make run         # launch with Tk mocks + control server
make test        # full unittest suite locally
make ship        # rsync the repo to the Pi + restart the service + status
make logs        # follow journalctl on the Pi
make state       # GET /state from the running app's control server
make frame       # save and open the current 320×240 frame
make e2e         # end-to-end smoke against the running service
```

`make ship` is the typical loop after editing code. After a version
bump in `pyproject.toml`, run `make sync` so the Pi's installed package
metadata refreshes.

The deploy uses `rsync --delete` with a fixed set of excludes
(`.git`, `.venv`, `__pycache__`, `runtime`). The `--delete` flag is
load-bearing: without it, files removed from the repo linger on the Pi
and can mask follow-up cleanups.

### When a hardware step fails

Before assuming a software bug, confirm the hardware is healthy — most
remote failures are physical:

- Pi powered off or off the network.
- Sigma fp USB cable loose, or the camera not in
  **USB Mode → Camera Control**.
- TFT disconnected from the header.
- Dead power bank.

### Inspecting the Pi over SSH

Beyond the `make` targets, these one-liners help confirm the Pi and its
peripherals are healthy (replace `pi3` with your own SSH alias):

```bash
ssh pi3 'uname -a && uptime'                        # OS / load / uptime
ssh pi3 'systemctl status fp-lapse --no-pager'      # service state
ssh pi3 'sudo journalctl -u fp-lapse.service -f'    # live logs (Ctrl+C to stop)
ssh pi3 'ls -l /dev/fb* /dev/gpiochip*'             # framebuffer + GPIO present?
ssh pi3 'cat /sys/class/graphics/fb1/virtual_size'  # the panel — expect 320,240
ssh pi3 'gpioinfo gpiochip0 | head -40'             # GPIO line state
ssh pi3 'lsusb'                                      # is the Sigma fp attached?
ssh pi3 'dmesg | tail -50'                           # recent kernel messages
ssh pi3 'curl -s http://127.0.0.1:9999/state | python3 -m json.tool'  # live app state
```

### Boot-to-app on the Pi

The Pi boots straight into the app, full-screen, with no login prompt.
The mechanism (systemd unit, diverting the TTY off the framebuffer with
`fbcon=map:0`) is documented in the README under
"Installing on the Pi". Editing `/boot/firmware/cmdline.txt` or
`config.txt` can leave the Pi unbootable — always back the file up
first.

---

## Hardware helper scripts

`scripts/` holds standalone tools that complement the test suite:

- `check_camera.py` — validates the Sigma fp adapter against the real
  camera (connect, read/write params, optionally fire the shutter).
- `e2e_smoke.py` — drives the running service through its HTTP control
  surface and asserts shots / skips.
- `demo_display.py`, `demo_buttons.py` — Tk mock smoke tests for the
  display and button layers.
