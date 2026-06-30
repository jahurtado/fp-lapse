# fp-lapse — functional reference

> The source-of-truth reference for fp-lapse's behavior and data
> model. All sections describe the live system: the data model
> (`configs.py`), engine (`engine.py`), UI (`ui/`) and orchestrator
> (`app.py`) match what's described here. Section numbers (`§N`) are
> cited from the code's docstrings, so they are kept stable.
>
> For a task-oriented walkthrough of operating the device, see the
> [user guide](user-guide.md).

---

## 1. Summary

`fp-lapse` keeps a **list of timelapse configurations** persisted to
disk. The user navigates the list on the TFT, runs one, and can switch
between them hot — without losing the temporal grid of the trigger
schedule. They can also edit, duplicate, create, and delete
configurations from the Pi's buttons.

There is no automatic time-based scripting (no events scheduled for
absolute timestamps). Every configuration change is manual, triggered
by pressing OK on the desired configuration.

---

## 2. Concepts

- **Timelapse configuration**: an entry in the list. Has a name, an
  **interval in seconds**, and a **shots** specification (see below).
- **Mode** (implicit in the shape of `shots`):
  - **Auto** (`shots == []`): the camera meters every shot. Exactly
    one photo per interval, with `ExposureMode=ProgramAuto` set on
    the camera.
  - **Manual** (`shots` non-empty, 1..9 elements): each element is
    a `Shot` with explicit `shutter`, `iso`, and optional `aperture`.
    Exposure mode is `Manual` and the engine sets every parameter
    before firing.
- **Shot**: an explicit capture description used only in manual mode.
  Fields are `shutter` (seconds, required), `iso` (integer, required),
  `aperture` (f-stop, optional — `null` means "lens default", useful
  for manual lenses without electronic aperture control).
- **Bracketing**: a manual configuration with more than one shot. The
  bracket's shots fire **back-to-back** inside the same grid tick, in
  the order they appear in the list.
- **Temporal grid**: the series of instants `t0, t0+p, t0+2p, …` where
  `p` is the interval of the configuration currently running and `t0`
  is the moment OK was first pressed. See §4.
- **Selected configuration**: the one the list cursor highlights.
  Rendered with a light-gray inverse-video band and a yellow bar on
  the left edge (see §7.0).
- **Running configuration**: the one the engine is firing. Marked with
  a red `●` next to the name and a sub-line `taken N   next in X.Xs`
  (see §7.0). Selection and execution may or may not coincide.

---

## 3. Data model

### 3.1 Single file

The entire list is persisted as one JSON file: `runtime/configs.json`.
If it does not exist at startup, it is created with `configs: []`.

### 3.2 Schema

```json
{
  "version": 2,
  "configs": [
    {
      "name": "Partial",
      "interval_s": 10,
      "shots": [
        { "shutter": "1/1000", "iso": 200, "aperture": null }
      ]
    },
    {
      "name": "Totality",
      "interval_s": 5,
      "shots": [
        { "shutter": "1/500", "iso": 400,  "aperture": null },
        { "shutter": "1/125", "iso": 400,  "aperture": null },
        { "shutter": "1/30",  "iso": 400,  "aperture": null },
        { "shutter": "1/8",   "iso": 400,  "aperture": null },
        { "shutter": 2,       "iso": 1600, "aperture": null }
      ],
      "start": { "date": "2026-08-12", "time": "11:33:23" },
      "end":   { "date": "2026-08-12", "time": "11:36:09" }
    },
    {
      "name": "Auto day",
      "interval_s": 30,
      "shots": [],
      "start": { "date": null, "time": "07:00:00" },
      "end":   { "date": null, "time": "19:00:00" }
    }
  ]
}
```

Rules:

- `version`: integer, today `2`. Allows future format migrations. The
  loader still accepts `version: 1` files (everything written before
  the scheduled-configs feature); they load with `start = end = null`
  and the next save rewrites them as `version: 2`.
- `name`: string, unique within the file. Used as identifier in logs
  and as the visible label. Max 20 chars.
- `interval_s`: number (integer or decimal) > 0, in seconds.
- `shots`: list with **0 to 9** elements.
  - **Empty** (`[]`) marks the config as **auto mode**: the engine
    fires exactly one photo per interval, with the camera in
    `ExposureMode=ProgramAuto` (meters everything itself). No `Shot`
    fields apply.
  - **Non-empty** marks the config as **manual mode**: every element
    is an explicit `Shot` (see field rules below). The engine sets
    `ExposureMode=Manual` and pushes the shot's shutter / iso /
    aperture before each fire.

  Per-parameter `"auto"` and `null` sentinels are **not** part of the
  data model. Auto is a config-level decision, not a per-parameter
  one — this collapses what used to be 8 combinations of mode mixing
  into two clean modes, and removes a class of camera-side surprises.

- `shutter`: one of:
  - fraction string: `"1/500"`, `"1/8000"`, `"1/30"` (numerator must
    always be `1`)
  - decimal/integer string: `"2"`, `"0.5"`, `"30"` (value in seconds)
  - numeric: `0.5`, `2`, `30` (value in seconds)

  The parser normalises every form to seconds (float). Accepted
  range: the camera's (Sigma fp: 1/8000 .. 30 s).

  **UI display rules:**
  - If the value is exactly `1/N` with N integer: `1/N`. E.g.
    `0.002` → `1/500`, `0.5` → `1/2`.
  - If the value is ≥ 1 second: `N s`. E.g. `2` → `2 s`, `30` →
    `30 s`.
  - Otherwise: `0.NNN s`. E.g. `0.333` → `0.333 s`.

- `iso`: integer in `[100, 25600]`. The UI cycles only through full
  native stops: `{100, 200, 400, 800, 1600, 3200, 6400, 12800,
  25600}`. The fp supports 1/3 EV intermediates and an extended
  range outside `[100, 25600]`; the JSON validator is the hard
  boundary, so a hand-edit can use intermediate values inside the
  native range.

- `aperture`: number (f-stop, e.g. `5.6`) **or** `null`. `null`
  means "manual lens, the camera doesn't drive aperture
  electronically — whatever the physical ring is set to is used".
  Useful (and common) for the fp because vintage adapted glass
  doesn't expose aperture over PTP.

  **UI display rules:**
  - Concrete value: `f/N` with one decimal when needed. E.g. `5.6`
    → `f/5.6`; `8` → `f/8`; `1.4` → `f/1.4`.
  - `null` → `f/—`.

  `iso` displays as `ISO N`.

- `start` / `end`: each is either `null` or an object of shape
  `{"date": "YYYY-MM-DD" | null, "time": "HH:MM:SS"}`. The `time`
  field is mandatory when the object is present; `date` may be `null`
  (daily recurrence) or an ISO date (one-shot absolute instant). See
  §11 for the semantics of `start` / `end` and how the schedule engine
  consumes them.

### 3.3 Translation to the camera

For each grid tick the engine performs one of two actions on the
`Camera` Protocol:

| Mode (config's `shots`) | What the engine does |
|---|---|
| `[]` (auto)         | One `set_params(exposure_mode=PROGRAM)` followed by one `shoot()`. |
| 1..9 manual shots   | For each shot: `set_params(shutter_s=…, iso=…, aperture=…, exposure_mode=MANUAL)` then `shoot()`. |

The exposure mode is set **on every fire**, not once at connect. That
way moving the physical dial mid-session is recovered automatically
on the next shot — the engine doesn't rely on the body's dial
position. `set_params` always receives numeric values (or `None` for
optional aperture).

### 3.4 Supported cameras and auto-detection

Two camera bodies are supported, both over USB-PTP:

- **Sigma fp** — via `sigma-ptpy`. Mode *USB Mode → Camera Control*.
- **Nikon D5600** — via `gphoto2` / `libgphoto2`. Trigger-only: shots
  are saved to the camera's SD card, never downloaded to the Pi.

The adapter is chosen by USB **VID/PID** — read from the device
descriptors, before any PTP library opens the bus. This matters because
both bodies are PTP-class devices: a library that grabbed "the PTP
camera" by class would seize whichever is plugged in (including the
Nikon) and drive it with the wrong commands. Selection happens at
startup **and at runtime** — unplugging one body and plugging in the
other re-detects and swaps the adapter automatically (**hot-swap**)
within a few camera-health ticks, no restart. Mid-swap a single shot
may fail (counted as a skip); the timelapse continues on its grid.

Precedence: the `FP_LAPSE_CAMERA` env var
(`mock` | `sigma_fp` | `nikon_d5600`, subsuming the legacy
`FP_LAPSE_MOCK=1` = `mock`) forces a specific adapter; otherwise on
macOS the mock is used; otherwise auto-detect by VID/PID. **With both
bodies attached, the Nikon wins** by default (overridable via the env
var).

**D5600 exposure dial is read-only over USB.** The engine still calls
`set_params(exposure_mode=…)` every fire, but the Nikon adapter cannot
change the physical mode dial — it only reads it. For deterministic
manual exposure (the primary eclipse path), **set the dial to M**. If
the engine wants `MANUAL` but the dial is elsewhere (or wants
`PROGRAM`/auto but the dial is on M), the run is **not blocked**
(warn-and-continue): the camera fires in whatever mode the dial is set
to, and a `DIAL NOT ON M` warning appears in the status bar (§7.1). The
Sigma fp, by contrast, can be forced to Manual programmatically and has
no such indicator.

---

## 4. Time synchronization

### 4.1 Grid and `t0`

- The moment the user presses OK on a configuration while the engine
  is stopped, `t0 = monotonic()` is set and the grid is born with the
  period set to that configuration's `interval_s`.
- The ideal capture instants are `t0 + k·p_current` for `k = 0, 1, 2,
  …`.
- The grid clock is **monotonic** (`time.monotonic()`). System clock
  adjustments (NTP, hotspot, manual change) do **not** desync the
  grid. Wall-clock time (`datetime.now()`) is only used for display
  and for log timestamps.

### 4.2 Hot configuration switch

When the user presses OK on a configuration different from the one
running:

- `t0` is preserved.
- The new period `p_new` applies from now on.
- The next shot fires at the smallest `t0 + k·p_new` that is **≥** the
  current instant.
- If the new configuration has a bracket, the whole bracket fires
  starting at that instant.

Example (the originating spec scenario): `t0 = 10:35:00`, config A
`10s / 2 shots`. At `+24s` the user presses OK on config B
`5s / 3 shots`. Captures:

| Instant   | Config | Shots |
|-----------|--------|-------|
| 10:35:00  | A      | 2     |
| 10:35:10  | A      | 2     |
| 10:35:20  | A      | 2     |
| (10:35:24 — user switches to B)            |
| 10:35:25  | B      | 3     |
| 10:35:30  | B      | 3     |
| 10:35:35  | B      | 3     |
| …                                          |

### 4.3 `t0` reset

`t0` is lost (and will be set again on the next OK) in these cases:

- The user stops the run with ESC (see §5.3) and confirms.
- The user deletes the running configuration from the manage menu
  (see §7.5). The Delete confirmation also acts as a Stop
  confirmation.
- Fatal engine error that aborts the run (see §6).
- App shutdown / restart.

### 4.4 No bracket interruption

Once the shots corresponding to a tick start, the bracket **completes
fully** before looking at the next tick. If during the bracket
`t = t0 + (k+1)·p` or later is reached, that tick is counted as a
"skip" (see §5). PTP does not allow clean cancellation of an in-flight
`shoot()`.

### 4.5 Switching configs during a bracket

If the user presses OK on a different configuration while a bracket is
firing, the change applies **after the current bracket ends**. The
in-flight bracket completes with the old config. This avoids
inconsistent intermediate states.

---

## 5. Engine behavior

### 5.1 States

- **IDLE**: no `t0`. Waiting for the user's OK.
- **RUNNING**: there is a `t0`. Alternating between waiting for the
  next tick and running the current tick's bracket.

There is no pause state. There is no "done" state (timelapses run
indefinitely until the user stops them or the Pi shuts down).

### 5.2 SKIPS

A **skip** is a grid tick lost because the previous bracket hadn't
finished. Counted **per missed grid instant**, not per individual
shot.

Example: interval 10s, bracket that takes 25s in total. The ticks at
+10s and +20s relative to the bracket start are lost → 2 skips. The
next shot fires at the smallest `t0 + k·p ≥ t_bracket_end`.

Rules:

- The SKIPS counter is a **cumulative total** while `t0` is valid. It
  resets when `t0` resets (§4.3).
- Each skip is logged with timestamp, index `k`, and context.
- It is displayed in the status bar of the main screen (§7.1).
- The app **never** stops working because of skips. The grid
  synchronization is always preserved.

### 5.3 Stopping with ESC

While the engine is RUNNING, pressing ESC asks for confirmation
before stopping (overlay §7.4). If confirmed:

- The engine moves to IDLE.
- `t0` is discarded.
- The SKIPS counter resets.
- If a bracket was in progress, it **completes** (already-started
  captures are always finished). The stop takes effect after that
  last capture.

If the confirmation is cancelled, the engine continues unchanged (the
dialog does not affect synchronization).

---

## 6. Errors

### 6.1 Camera

When `CameraNotConnected`, `CameraBusy`, `CaptureFailed`, or a timeout
fires during `shoot()` or `set_params()`:

- Logged with detail (cause, tick `k`, shot index within the bracket).
- A **short buzzer beep** is emitted (~150 ms).
- The engine **does not abort**. It continues the grid at the next
  reachable tick. The failed shot is not retried within the same tick.
- After **5 consecutive failures** (with no successful capture in
  between) a persistent banner is shown on the main screen: `CAMERA
  NOT RESPONDING`. The engine keeps trying.
- The banner clears automatically as soon as a successful capture
  returns. The consecutive-failure counter resets to 0 on each
  success.

### 6.2 Validation errors when editing

When saving an edit or creating a new configuration, if the values
provided are invalid (interval ≤ 0, empty shots list, value out of
the camera's range, duplicate name), an explanatory message is shown
and the user stays in edit mode. **No buzzer** (this is not an
operational error).

### 6.3 Corrupt JSON file at startup

If `configs.json` exists but can't be parsed or doesn't match the
schema:

- Renamed to `configs.json.bak.<timestamp>`.
- App starts with `configs: []`.
- Logged as ERROR.
- Long buzzer beep (~1s) as soon as the UI starts, plus a persistent
  `CONFIGS RESET` banner on the main screen until the user creates or
  edits something.

---

## 7. Interface (TFT 320×240, 6 buttons)

Every string shown in the UI is **in English**. We assume a monospace
font (Menlo on Mac dev, DejaVuSansMono on the Pi) at 11 px for body
text. The wireframes below are approximate — actual width is whatever
fits in 320 pixels with that font.

### 7.0 Visual indicators (selected vs running)

The two states are visually very distinct:

- **Selected** (configuration under the cursor): the name row and all
  its shot rows render with a **highlighted band background** (inverse
  video: light background, dark text) and a **`▌` (vertical bar) on
  the left margin** spanning the full block height.
- **Running** (configuration the engine is firing): a **`●` (filled
  red circle)** immediately to the left of the name, plus an
  additional sub-line below the name with `taken N   next in X.Xs`.

In plain-text wireframes the `▌` and `●` are drawn as such; the band
is represented by repeating `▌` on each affected line. The two
indicators can coexist (the running config can also be selected):
you'll see `▌●` on the name line and `▌` on each shot line.

Shot row format:

- 3 aligned columns: shutter, ISO, aperture.
- shutter with no prefix (`1/1000`, `2 s`, `auto`, `—`).
- ISO with `ISO` prefix (`ISO 200`, `ISO auto`, `ISO —`).
- aperture with `f/` notation (`f/5.6`, `f/auto`, `f/—`).
- `—` means `null` ("don't touch"); `auto` is written literally.
- If the bracket has >1 shot, each shot line shows the index `1`–`N`
  at the start.

### 7.1 Main screen — IDLE (engine stopped)

```
┌────────────────────────────────────────┐
│ 18:42:07  fp ●                         │ ← status bar (IDLE)
├────────────────────────────────────────┤
│▌Partial               10 s · 1 shot    │ ← selected (band + ▌)
│▌  1/1000   ISO 200    f/—              │
│                                        │
│ Totality                5 s · 5 shots  │
│  1  1/500   ISO 400    f/—             │
│  2  1/125   ISO 400    f/—             │
│  3  1/30    ISO 400    f/—             │
│  4  1/8     ISO 400    f/—             │
│  5  2 s     ISO auto   f/—             │
│                                        │
│ Free daytime          30 s · 1 shot    │
│    —        ISO —      f/—             │
│                                        │
│ + New configuration                    │
├────────────────────────────────────────┤
│ ↑↓ nav    OK run    hold OK menu       │
└────────────────────────────────────────┘
```

Conventions:

- The user cursor navigates **by configuration** (not by individual
  shot). `↑` / `↓` jump from one config name to the next, dragging
  the highlight band that covers the name + shots.
- `+ New configuration` is a pseudo-item at the end of the list, also
  selectable. Pressing OK on it enters edit mode directly with a new
  default config (`interval_s=10`, one shot with all `null`).
- The list **scrolls vertically** if it doesn't fit on screen. The
  selected configuration is always kept visible.

**Status bar** (always visible, on top):

- Local wall-clock time.
- The **live camera model label** (`fp` for the Sigma, `D5600` for the
  Nikon) followed by `●` (green, connected) or `●` red (not connected).
  The label updates automatically when the body is hot-swapped (§3.4).
- A `DIAL NOT ON M` warning (WARN amber) when the Nikon D5600's
  read-only mode dial disagrees with the exposure mode the engine
  requested (§3.4). Not shown for the Sigma fp.
- `SKIPS N` only if the engine is RUNNING or if there were skips in
  the last run since the last boot.

> Note: the Sigma fp's battery level is **not shown** because
> `sigma-ptpy` doesn't expose that data; the real adapter returns
> `None`. The Nikon adapter *does* fill `battery_pct` (from gphoto2's
> `batterylevel`), but it is not yet surfaced in the status bar. The
> user checks battery state on the camera itself.

**Button mapping in IDLE:**

| Button         | Action                                                  |
|----------------|---------------------------------------------------------|
| ↑ / ↓          | Moves the cursor to the previous/next config.           |
| ←              | Opens the **SETTINGS** menu (§7.6.1).                    |
| →              | Toggles the schedule on/off.                            |
| OK short       | Runs the selected config (or creates, if on `+ New configuration`). |
| OK long (≥3s)  | Opens the manage menu (§7.5). Does not apply on `+ New configuration`. |
| ESC           | **No effect.** The main screen is the root; there is nowhere to go back to. |

The main-screen footer carries a second, always-present line with the
global shortcuts: `← settings   → sched on/off   OK+ESC shutdown`.

### 7.2 Main screen — RUNNING (engine firing)

Same layout as IDLE; indicators and bottom hint change.

Case A — cursor sits on the running config (both coincide):

```
┌────────────────────────────────────────┐
│ 18:42:13  fp ●                 SKIPS 0 │
├────────────────────────────────────────┤
│▌●Totality               5 s · 5 shots  │ ← running + selected
│▌  taken 142   next in 4.3s             │
│▌  1  1/500   ISO 400   f/—             │
│▌  2  1/125   ISO 400   f/—             │
│▌  3  1/30    ISO 400   f/—             │
│▌  4  1/8     ISO 400   f/—             │
│▌  5  2 s     ISO auto  f/—             │
│                                        │
│ Partial               10 s · 1 shot    │
│    1/1000   ISO 200    f/—             │
│                                        │
│ + New configuration                    │
├────────────────────────────────────────┤
│ ↑↓ nav                    ESC stop    │
└────────────────────────────────────────┘
```

Case B — cursor sits on **another** config (not the running one):

```
┌────────────────────────────────────────┐
│ 18:42:13  fp ●                 SKIPS 0 │
├────────────────────────────────────────┤
│ ●Totality               5 s · 5 shots  │ ← running, not selected
│   taken 142   next in 4.3s             │
│   1  1/500   ISO 400   f/—             │
│   2  1/125   ISO 400   f/—             │
│   3  1/30    ISO 400   f/—             │
│   4  1/8     ISO 400   f/—             │
│   5  2 s     ISO 1600  f/—             │
│                                        │
│▌Partial               10 s · 1 shot    │ ← selected, not running
│▌  1/1000   ISO 200    f/—              │
│                                        │
│ + New configuration                    │
├────────────────────────────────────────┤
│ ↑↓ nav   OK switch here   ESC stop    │
└────────────────────────────────────────┘
```

**Button mapping in RUNNING:**

| Cursor on…                  | OK short                                       | OK long             | ESC                      |
|-----------------------------|------------------------------------------------|---------------------|---------------------------|
| the running config          | No effect.                                     | Opens manage menu.  | Stop confirmation.        |
| another config              | Switches the engine to that config (keeps `t0`). | Opens manage menu.  | Stop confirmation.        |
| `+ New configuration`       | Opens edit (does not stop the engine).         | (n/a)               | Stop confirmation.        |

`↑` / `↓` always move the cursor; `←` / `→` are reserved.

### 7.3 Edit screen

```
┌────────────────────────────────────────┐
│ EDIT · Totality                        │
├────────────────────────────────────────┤
│   name                Totality         │
│   interval                5 s          │
│   shots                   5            │
│ ─────────────────────────────────────  │
│▌ #1 shutter            1/500           │ ← cursor (active field)
│  #1 iso                 400            │
│  #1 aperture             —             │
│  #2 shutter            1/125           │
│  #2 iso                 400            │
│  …                                     │
├────────────────────────────────────────┤
│ ↑↓ field   ←→ value   OK save   ESC   │
└────────────────────────────────────────┘
```

In auto mode (`shots == []`) the per-shot rows collapse:

```
│   name                Auto day         │
│   interval               30 s          │
│▌  shots               1 (auto)         │
│                                        │
│   camera meters every shot             │
```

**Navigation:**

- `↑` / `↓`: move the cursor to the previous/next field. Consistent
  with the rest of the screens (main list, manage menu), where ↑/↓
  always means "move cursor".
- `←` / `→` on the active field: cycles the value (previous / next of
  the discrete list). "Scrubbing" convention — left decreases, right
  increases.
  - `name`: no effect (read-only from the UI — §7.6).
  - `interval`: cycles through the discrete list
    `{1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 300, 600}` seconds.
  - `shots`: cycles through `{1 (auto), 1, 2, 3, 4, 5, 6, 7, 8, 9}`
    with wrap. `1 (auto)` corresponds to the empty-shots auto mode
    (see §3.2). Cycling from manual N to auto preserves the manual
    shots in an edit-session snapshot — going back from auto to N
    restores them, including individual values the user edited
    inside manual. A newly added shot (when growing past the
    snapshot's length) **inherits the previous shot's values**.
  - `#N shutter`: cycles through the fp's valid shutters
    `{"1/8000", "1/6400", …, 30}` (no `null` / no `"auto"` — those
    sentinels were removed from the data model).
  - `#N iso`: cycles through `{100, 200, 400, …, 25600}` (full stops;
    see §3.2).
  - `#N aperture`: cycles through `{null, 1.4, 1.6, 1.8, …, 22}` —
    `null` (the wrap-around slot) means "manual lens, no electronic
    aperture control".
- `OK`: opens the Save confirmation overlay (§7.4). Saving is an
  irreversible operation, so the spec prioritizes safety over speed.
  The confirmation fires even when there are no apparent changes (no
  special-case "nothing dirty" path — keeps the rule uniform).
- `ESC`: opens the discard-changes confirmation (§7.4) if there were
  changes; returns directly to the list if there were none.

### 7.4 Confirmation overlays

All overlays share format and button mapping.

```
┌────────────────────────────────────────┐
│                                        │
│       Stop the timelapse?              │
│       Sync will be lost.               │
│                                        │
│         OK yes        ESC no          │
│                                        │
└────────────────────────────────────────┘
```

Cases where they are shown:

| Context                                   | Text                              |
|-------------------------------------------|-----------------------------------|
| ESC with engine in RUNNING               | `Stop the timelapse?` / `Sync will be lost.` |
| OK in edit                                | `Save changes?`                   |
| ESC in edit with pending changes         | `Discard changes?`                |
| Manage menu → Delete                      | `Delete 'X'?`                     |

The overlay is laid on top of the previous screen, darkening the
background. `OK` confirms, `ESC` cancels. There is no third button.

> Intentional consequence: in RUNNING, `ESC` (opens overlay) +
> `ESC` (cancels) is effectively a no-op. This is desirable — an
> accidental press does not break synchronization. Whoever truly
> wants to stop presses `ESC` → `OK`.

### 7.5 Manage menu (long-press OK)

Opens with OK held ≥3 seconds on an existing configuration (not on
`+ New configuration`). Available in both IDLE and RUNNING; does not
affect the engine in either case.

```
┌────────────────────────────────────────┐
│  Totality                              │
│  ────────────────────────────────────  │
│▌ Edit                                  │
│  Duplicate                             │
│  Delete                                │
│  Cancel                                │
└────────────────────────────────────────┘
```

- **Edit**: enters the edit screen (§7.3).
- **Duplicate**: creates a copy with the ` (copy)` suffix and inserts
  it right after the original. Returns to the list with the copy
  selected.
- **Delete**: asks for confirmation. If confirmed, removes the entry.
  If the deleted config was running, the engine moves to IDLE and
  `t0` is lost (the Delete confirmation already serves as a Stop
  confirmation; no second prompt).
- **Cancel**: closes the menu.

`↑` / `↓` navigate, `OK` selects, `ESC` closes the menu without
action.

### 7.6 Configuration names (no on-screen keyboard)

Editing text with 6 buttons is tedious enough to not be worth the
effort. Decision:

- The `name` field is shown in the edit screen (§7.3) **read-only**:
  visible for identification, but `←/→` on it does nothing.
- **`+ New configuration`** creates the entry with an auto-generated
  name: `Config 1`, `Config 2`, … (the next free integer that doesn't
  collide with an existing name).
- **Duplicate** (§7.5) generates `<original> (copy)`; if that already
  exists, `<original> (copy 2)`, etc. If the concatenation exceeds
  the 20-char hard limit, the original is truncated to make room for
  the suffix.
- Renaming a configuration requires editing `runtime/configs.json`
  externally (via `ssh pi3`) and restarting the app — persistence is
  read-on-startup (§8).

This is accepted as a conscious limitation. The on-screen keyboard now
exists (added for Wi-Fi passwords — §7.6.1) and is built generically,
but it is **not yet wired to configuration-name editing**: renaming a
configuration still requires editing the JSON externally. Wiring the
keyboard to name editing is a possible future enhancement.

#### 7.6.1 SETTINGS menu, on-screen keyboard & Wi-Fi setup

The main-screen **LEFT** button opens a flat **SETTINGS** menu (one
level, no submenus) with three items:

```
   ┌──────────────────┐
   │  SETTINGS        │
   │──────────────────│
   │ ▸ Sync Time (NTP)│   ← force an NTP sync of the device clock
   │   Set Time (Manual)│ ← enter the time/date by hand (digit picker)
   │   Wi-Fi setup    │   ← scan → pick → keyboard → connect
   └──────────────────┘
```

`Sync Time (NTP)` and `Set Time (Manual)` act on the **device clock**;
`Wi-Fi setup` opens the Wi-Fi flow. `ESC` closes the menu.

**On-screen keyboard.** The first on-screen text-entry sub-screen. An
**alphabetical grid** (not QWERTY — easier to navigate with a 6-button
D-pad), with a **layer key** that cycles `abc → ABC → 123 → #+=` so the
four layers together reach every printable ASCII character. Special
keys: `␣` (space), `⌫` (backspace — delete the last character), `◉`
(show/hide, password only — entry is masked by default so the operator
can verify a tricky password before committing), and `✓` (Done —
commits the text). **ESC cancels the whole entry** (it never deletes a
character — that is `⌫`). UP/DOWN/LEFT/RIGHT move the cursor over the
grid (LEFT/RIGHT wrap within a row); OK types the highlighted key.
Passwords are validated as 8–63 characters (WPA2-PSK), SSIDs as 1–32
bytes.

**Wi-Fi flow.** `Wi-Fi setup` shows a **cached** scan of nearby
networks (signal-strength glyph, a lock marker for secured networks, a
green dot on the active one), with trailing `Other network…` (type a
hidden SSID) and `Rescan` (force a fresh scan — slower, off-thread,
shows a `Scanning…` animation) items.

Each network row carries four distinct gestures (short vs. long is
distinguished by firing OK/BACK on **release**):

- **OK (short) — connect.** An open network, or a secured network whose
  password is **already saved**, connects immediately reusing the
  stored credentials (no keyboard). A secured network with **no** saved
  profile opens the keyboard for its password first.
- **OK (hold) — edit password.** On a secured network (saved or not),
  opens the keyboard to type a new password; on commit it connects,
  creating or replacing the profile. On an open network it is a no-op.
- **ESC (short) — back** to the SETTINGS menu.
- **ESC (hold) — forget.** Only on a **saved** network: opens a
  confirmation overlay that deletes its NetworkManager profile.

`Other network…` and `Rescan` respond to a short OK only. The connection
runs **off the UI thread** with a **30 s timeout** and a `Connecting…`
animation; the result screen shows either *Connected* (with the obtained
IP) or a clear failure reason (wrong password, not in range, timed out).
**On failure the previous connection is left untouched** — the operator
simply retries.

This is a **setup-time** action (so the box can reach NTP on a new
network); it does not affect the capture loop's offline guarantee.
WPA-Enterprise, captive portals, hidden *open* networks, static IP and
a full profile manager are out of scope.

### 7.7 Hard limits

- Maximum configurations in the list: **20**.
- Maximum shots per bracket: **9** (single digit).
- Maximum `name` length: **20 characters**.

**Behavior when reaching the limit:**

- **20 configs**: the `+ New configuration` pseudo-item is still
  shown but dimmed (`DIM` color); pressing OK on it shows an
  ephemeral overlay (~1.5 s, no buttons) with the text
  `Config limit reached (20)`. Analogous for `Duplicate` from the
  manage menu.
- **9 shots in a bracket**: in the edit screen, `→` on the `bracket
  size` field does nothing (no overlay; the limit is implicit in the
  value not increasing).
- **20 characters in `name`**: not reachable from the UI (no
  on-screen keyboard — §7.6). The `(copy)` suffix from `Duplicate` is
  guaranteed within the limit by truncating the original as needed.

If the external JSON violates these limits, the first N are loaded
and a WARNING is logged — startup is not blocked.

### 7.8 Safe shutdown (ESC + OK chord)

The Pi has no power switch. Cutting the powerbank live risks
filesystem corruption on the SD (observed in the field: a hard pull
during a write left the rootfs in a state that needed reflashing).
The app provides a deterministic clean-shutdown path from the
operator panel.

**Trigger**: ESC and OK held **simultaneously** for **3 seconds**
(the same threshold as the long-press in §7.5). Available from
**any** screen — the chord is global, not gated by `AppState`. No
visual feedback during the hold; the operator simply holds.

Releasing either button before 3 s aborts the chord and nothing
happens. There is no warning, no toast, no partial state — the
chord is silent until it fires.

**Confirmation overlay** (same layout as §7.4):

```
┌────────────────────────────────────────┐
│                                        │
│            Power off?                  │
│                                        │
│         OK yes        ESC no          │
│                                        │
└────────────────────────────────────────┘
```

`ESC` returns to the screen the operator was on (including engine
state — the running timelapse is **not** stopped by opening or
cancelling this overlay). `OK` proceeds.

**Visual after confirm**:

A single screen with the title `POWERING OFF…` (green) and the hint
`Unplug the powerbank when the green LED is off.` (dim). Painted from
the moment `OK` is pressed on the overlay and held throughout the
entire shutdown sequence — including after the kernel halts, because
the pitft22 retains its last frame in panel memory until 3.3 V is cut
at the GPIO header.

> An earlier design used two phases (`SHUTTING DOWN…` then `SAFE TO
> DISCONNECT`). In practice systemd starts firing SIGTERM at the
> service within a few hundred ms of `/sbin/shutdown -h now`, so the
> first phase was visible for ~200 ms — too brief to read. A single
> always-correct message removes the timing race and the cosmetic
> sleep that would otherwise be needed to make phase 1 readable.

**Engine and camera interaction**:

- If the engine is RUNNING, it gets the normal STOP path during
  systemd shutdown (the existing SIGTERM handler in `__main__.py`).
  Sync data for the in-progress bracket is lost — same as a §5.3
  user-initiated stop.
- The camera session is closed cleanly (`SigmaCloseApplication`).

**Why no countdown / progress indicator during the 3 s hold?**

The chord is deliberate by design (two buttons + duration). A
half-pressed chord that crosses 1.5 s and is released is
indistinguishable from intent to abort, which is the conservative
interpretation. Adding a progress bar would invite the operator to
"watch and decide" mid-hold, which raises the false-positive rate.
The explicit confirmation overlay after the 3 s catches honest
mistakes.

**Failure modes**:

- `/sbin/shutdown` not found / non-zero exit: the screen stays on
  Phase 1, a WARNING is logged. The operator can SSH in to
  diagnose. No automatic retry.
- Service not running as root: the `subprocess` call fails with
  EPERM and the same Phase-1-stuck behavior applies. (In practice
  the unit always runs as root — §execution model in CLAUDE.md.)

---

## 8. Persistence

- `runtime/configs.json` is the single source of truth.
- Every operation that modifies the list (create, edit, duplicate,
  delete) performs an **atomic** write: writes to `configs.json.tmp`
  then renames. This avoids leaving the file half-written if power is
  cut.
- A simple rotating backup is kept: before each write, the current
  file is copied to `configs.json.bak` (no timestamp; only the last
  one). This gives trivial rollback if the user makes a destructive
  mistake.
- The file is editable externally by hand (via `ssh pi3`). On the
  next app start, the changes take effect. The app does **not**
  watch the file live: it only reads it at startup.

**Coexistence with corruption rescue** (§6.3): two distinct files
live under `runtime/`, never colliding:

| File                                | Origin                                  | When                                     |
|-------------------------------------|-----------------------------------------|------------------------------------------|
| `configs.json.bak`                  | Rotating backup (this §)                | Overwritten before every modification.   |
| `configs.json.bak.<YYYYMMDD-HHMMSS>`| Rescue of corrupt JSON at startup       | Created only when the main file doesn't parse; never overwritten. |

For manual recovery: copy whichever applies back to `configs.json`
and restart the app.

---

## 9. Startup

When launching the app:

1. Initialize display, buttons, buzzer (hardware layer).
2. Load `runtime/configs.json`. If it doesn't exist, create it
   empty. If it is corrupt, set it aside and start empty (§6.3).
3. Connect to the camera. If it fails, show `fp ✕` in the status bar;
   a background **camera-health thread** retries `connect()` every 5 s
   and actively probes for silent USB disconnects, flipping the
   indicator back to `fp ●` on success. The app is usable without a
   camera (you can navigate and edit; only OK-to-run reports an error
   and doesn't start).
4. Enter the main screen (§7.1) with the cursor on the first config
   (or on `+ New configuration` if the list is empty).
5. Engine in IDLE. No `t0`. No prior execution restored.

---

## 10. Out of scope (explicit declarations)

- **No** events scheduled for absolute instants (no "schedule a
  change for 18:30 UTC"). Every change is manual.
- **No** restored execution after a restart. If the Pi reboots, the
  engine starts in IDLE.
- **No** modal notifications with a long buzzer for operator actions
  ("REMOVE FILTER"). The operator knows the flow and operates the
  buttons manually. The buzzer is exclusively for errors (§6).
- **No** dry-run mode in this version. To test without firing the
  camera, the `mock` adapters are used during Mac development.
- **No** time synchronization done by the app. Wall-clock time is
  whatever it is; the grid uses a monotonic clock.
- **No** live observation of the JSON. Editing it by hand requires
  restarting the app to take effect.
- **No** text editing on the Pi (no on-screen keyboard). Names are
  auto-generated when creating/duplicating; renaming requires editing
  the JSON externally (§7.6).
- **No** fp battery level is shown. `sigma-ptpy` doesn't expose that
  data; the field is kept in the interface for the future but the
  real adapter always returns `None`.
