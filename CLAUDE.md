# CLAUDE.md — context for AI coding agents

Context for an AI coding assistant (e.g. Claude Code) working in this
repository: project conventions, the hardware map, and the
develop-on-a-computer → test-on-the-Pi workflow that defines how the
project is built and validated.

---

## Project summary

`fp-lapse` — intervalometer and timelapse controller for the Sigma fp
camera, running on a Raspberry Pi 3 + 2.2" TFT HAT (pitft22, ILI9340) +
6 GPIO buttons. Headline use case: the total solar eclipse of
2026-08-12. See `README.md` for functional detail and architecture, and
`docs/reference.md` for the exact behavior contract — the source of
truth the code implements (docstrings cite it by section number, e.g.
"§7.3 of docs/reference.md"; keep those numbers stable when editing it).

Naming: **`fp-lapse`** for the repo, CLI, and public name; **`fp_lapse`**
for the importable Python module.

---

## Development model: develop on a computer, test on the Pi

Day-to-day development happens on a regular computer (the project was
built on a Mac), **not** on the Raspberry Pi:

- The whole UX — navigation, editing, the manage menu, the confirmation
  overlays — runs on the desktop through Tk mocks for the display, the
  buttons, and the camera. No hardware is required to work on app logic
  or the UI. `FP_LAPSE_MOCK=1` forces mock mode even on Linux.
- The Pi 3 holds the real hardware (TFT, buttons, Sigma fp). It runs a
  32-bit OS with 1 GB of RAM and is used for hardware validation only.
  All Pi-side work is done remotely over SSH.

### Deploying to the Pi

Configure an SSH alias (e.g. `pi3`) in your `~/.ssh/config` pointing at
your Pi (key-based, no password). The `Makefile` wraps the routine
commands — `make ship` (rsync + restart + status), `make logs`,
`make state`, `make frame`, `make e2e`; override the host per-invocation
with `PI=otherhost make ship`.

The deploy is an rsync that mirrors the tree:

```bash
rsync -av --delete \
    --exclude '.git' --exclude '.venv' \
    --exclude '__pycache__' --exclude 'runtime' \
    ./ pi3:~/fp-lapse/
```

`--delete` is load-bearing: without it, files removed from the repo
linger on the Pi and can mask follow-up cleanups. The four excludes
protect runtime state, the venv, byte-compiled caches, and git history.

The Pi-side working directory is always `~/fp-lapse/`; the venv lives in
`~/fp-lapse/.venv/`, logs in `~/fp-lapse/runtime/`.

### Inspecting the Pi over SSH (debugging)

```bash
ssh pi3 'uname -a && uptime'
ssh pi3 'systemctl status fp-lapse --no-pager'           # service state
ssh pi3 'sudo journalctl -u fp-lapse.service -f'         # live logs (Ctrl+C to stop)
ssh pi3 'ls -l /dev/fb* /dev/gpiochip*'                  # framebuffer + GPIO present?
ssh pi3 'cat /sys/class/graphics/fb1/virtual_size'       # the panel — expect 320,240
ssh pi3 'gpioinfo gpiochip0 | head -40'                  # GPIO line state
ssh pi3 'lsusb'                                           # is the Sigma fp attached?
ssh pi3 'dmesg | tail -50'                               # recent kernel messages
ssh pi3 'curl -s http://127.0.0.1:9999/state | python3 -m json.tool'  # live app state
```

### When a hardware step fails

Before assuming a software bug, confirm the hardware is healthy — most
remote failures are physical:

- Pi powered off or off the network.
- Sigma fp USB cable loose, or the camera not in
  **USB Mode → Camera Control**.
- TFT disconnected from the header.
- Dead power bank.

Destructive changes on the Pi — `rm` outside `~/fp-lapse/runtime/`,
editing `/boot/firmware/*.txt`, installing/removing apt packages,
stopping services other than `fp-lapse` — warrant a confirmation first.

---

## Hardware (summary — full detail in README)

| GPIO BCM | Use |
|----------|-----|
| 7, 8, 9, 10, 11, 25 | TFT pitft22 (SPI + DC). **Do not touch.** |
| 23, 22, 24, 5 | D-pad buttons (UP, DOWN, LEFT, RIGHT) |
| 17, 4 | Side buttons (BACK, OK) |
| 18 | Buzzer (placeholder, not yet wired) |

Buttons: active-low, internal pull-up, 50 ms software debounce
(`gpiozero` on the **`lgpio`** backend — RPi.GPIO's edge detection is
broken on recent kernels, so `GPIOZERO_PIN_FACTORY=lgpio` is forced).

Framebuffer: 320×240, RGB565 little-endian. The app locates the panel by
driver name (`ili9340`) in `/sys/class/graphics/fb*/name`, so it does not
hard-code a device — on Trixie a firmware simple-framebuffer takes
`/dev/fb0` and the panel lands on `/dev/fb1` (it was `fb0` on Bookworm).
Rotation is configured in `/boot/firmware/config.txt`.

Camera: Sigma fp over USB-A↔USB-C, in mode *USB Mode → Camera Control*,
driven by `sigma-ptpy` (pure Python, no proprietary SDK).

---

## Execution model — full-screen boot, no login

The Pi boots directly into the app, taking over the full TFT, with no
login prompt or visible TTY behind it:

1. **systemd** launches `python -m fp_lapse` (`systemd/fp-lapse.service`)
   as root under `multi-user.target` (no graphical environment).
2. **The TTY is diverted off the panel** with `fbcon=map:0` in
   `/boot/firmware/cmdline.txt`: pinning the console to `fb0` keeps it
   off the panel (`fb0` is dropped headless and the console falls back to
   a dummy device). Optional for a silent boot:
   `quiet loglevel=3 vt.global_cursor_default=0` in the same file.
3. **No auto-login is needed** — the service is independent of any user
   session, and SSH keeps working normally.

Editing `/boot/firmware/cmdline.txt` or `config.txt` can leave the Pi
unbootable; back the file up first.

---

## Development tooling

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management and execution:

```bash
uv sync                                       # create .venv, install locked deps
uv run fp-lapse                               # run with Tk mocks
uv run python -m unittest discover -s tests   # the test suite (~0.3 s)
uv sync --extra pi                            # hardware deps (sigma-ptpy, gpiozero, lgpio), Pi only
```

Don't reach for `pip` or `python -m venv` directly — use `uv run X` /
`uv pip install Y` so the lockfile and the running environment stay in
sync. `sigma-ptpy` is not on PyPI; it resolves from the
[`makanikai/sigma-ptpy`](https://github.com/makanikai/sigma-ptpy) GitHub
repo at install time only — the runtime stays fully offline. On the Pi,
the armv7l wheels for `numpy`, `Pillow`, and `lgpio` come prebuilt from
piwheels.org so the Pi 3 doesn't compile from sdist.

---

## Testing

Every module under `src/fp_lapse/` ships with a `tests/test_X.py`
companion using stdlib `unittest` (each file does
`sys.path.insert(0, "src")` so it runs without an install step).

- **Bug fixes** ship with a regression test that fails without the fix
  and passes with it — never fix a bug silently.
- **Behavior changes** are reflected in `docs/reference.md` (the
  functional source of truth) first, then pinned with a test.
- **Visual regression** tests (`test_ui_*_screen.py`,
  `test_ui_overlays.py`, `test_ui_manage_menu.py`) compare rendered PIL
  bytes against the PNGs in `docs/mockups/`. When a render changes on
  purpose, regenerate them with
  `uv run python docs/mockups/render_mockups.py` and commit the code and
  the new images together.
- Don't merge code that lowers the test count without a clear reason.

Run the suite often — it's fast.

---

## Code conventions

- Python ≥ 3.11 (Trixie ships 3.13). Code under `src/fp_lapse/`, tests
  under `tests/`. All code, comments, docstrings, and documentation are
  written in **English** so anyone can read them without context
  translation.
- Layered, no circular dependencies: the hardware layer
  (`display` / `buttons`) has no dependencies; `camera`, `engine`, and
  `ui` sit above it; `configs` is a pure data model (dataclasses);
  `app.py` orchestrates.
- Event-driven, multi-threaded: a dedicated scheduler thread ticks the
  engine on each grid mark; a camera-health thread reconnects the camera
  on USB drop. All shared state is guarded by a single `app.lock`
  (RLock) — keep new cross-thread state behind it.
- Logs: a rotating file at `runtime/fp-lapse.log` plus stderr; every
  engine event and camera command is logged with a UTC timestamp.

---

## Project constraints

- **No internet at runtime.** The device runs fully offline.
- **No proprietary Sigma SDK** (x86_64 binaries; not viable on ARM
  32-bit). The camera is driven by `sigma-ptpy`, pure Python.
- **No RTC assumed**: boot cleanly with the wrong clock and allow manual
  correction; the engine uses monotonic time anyway.
- **Don't modify `/boot/firmware/config.txt` or `cmdline.txt`** without
  backing them up first — a malformed file leaves the Pi unbootable and
  the only fix is pulling the SD card.
