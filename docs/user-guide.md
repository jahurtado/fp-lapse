# fp-lapse — user guide

This guide explains how to **operate the device**: navigate the list of
timelapse configurations, run one, switch between them while running,
and create or edit your own — all from the six buttons on the Pi.

It is task-oriented. For the exact behavioral contract (data model,
grid math, error handling, hard limits) see the
[functional reference](reference.md); section numbers like §7.3 below
point into it.

---

## The device at a glance

fp-lapse turns a Raspberry Pi with a 2.2" TFT and six buttons into a
standalone intervalometer for the Sigma fp. It holds a **list of
timelapse configurations**; you pick one and run it, and the camera
fires on a fixed time grid until you stop it.

There is no menu of dates or scheduled events — every change is manual,
made by pressing a button. The app boots straight into the main screen;
there is no login or desktop behind it.

### The six buttons

| Button | In the list / menus            | While editing a value      |
|--------|--------------------------------|----------------------------|
| **↑ / ↓** | Move the cursor up / down    | Move between fields        |
| **← / →** | (reserved, no effect)        | Change the selected value  |
| **OK**    | Run / select / confirm       | Open the *save* dialog     |
| **BACK**  | Stop / go back / cancel      | Go back (discard dialog)   |

Two OK gestures matter throughout:

- **OK (short press)** — the primary action (run a config, confirm,
  select a menu item).
- **OK (long press, ≥ 3 s)** — opens the **manage menu** for the
  highlighted configuration (Edit / Duplicate / Delete).

---

## The main screen

```
┌────────────────────────────────────────┐
│ 18:42:07  fp ●                         │ ← status bar
├────────────────────────────────────────┤
│▌Partial               10 s · 1 shot    │ ← selected (highlight + ▌)
│▌  1/1000   ISO 200    f/—              │
│                                        │
│ Totality                5 s · 5 shots  │
│  1  1/500   ISO 400    f/—             │
│  …                                     │
│ + New configuration                    │
├────────────────────────────────────────┤
│ ↑↓ nav    OK run    hold OK menu       │
└────────────────────────────────────────┘
```

- The **status bar** (top) shows the local clock and the camera link:
  `fp ●` (green) = connected, `fp ✕` (red) = not connected. While a
  timelapse runs it also shows `SKIPS N` (see below).
- The **list** shows every configuration with its interval and its
  shots. `↑` / `↓` move the cursor **one whole configuration at a
  time** (the highlight band covers the name and all its shot rows).
- **`+ New configuration`** is a pseudo-item at the bottom of the list.
  Selecting it and pressing OK starts a brand-new configuration.

### Two indicators: *selected* vs *running* (§7.0)

These are independent, and both can apply to the same row:

- **Selected** — the row under the cursor. Drawn with a light
  highlighted band and a `▌` bar on the left edge.
- **Running** — the configuration the camera is currently firing.
  Marked with a red `●` next to its name and an extra line:
  `taken N   next in X.Xs`.

So the config you are *looking at* (selected) need not be the one that
is *firing* (running).

---

## Running a timelapse

1. Move the cursor (`↑` / `↓`) to the configuration you want.
2. Press **OK**. The camera takes the first shot immediately and a time
   grid is born: shots then fire every *interval* seconds.
3. The running config shows `taken N` (how many ticks have fired) and
   `next in X.Xs` (countdown to the next one).

The grid is **regular and self-correcting**: shots land on
`t0, t0+interval, t0+2·interval, …` where `t0` is the moment you first
pressed OK. The countdown uses a steady internal clock, so changing the
Pi's wall-clock time mid-session does **not** shift the grid (§4.1).

### Stopping

Press **BACK** while running. A confirmation appears
(`Stop the timelapse? / Sync will be lost.`). Press **OK** to confirm
or **BACK** to cancel.

- If a multi-shot bracket is mid-fire when you confirm, it **finishes**
  the current bracket before stopping — captures already started are
  never cut off (§5.3).
- An accidental single BACK does nothing on its own: BACK opens the
  dialog, BACK again cancels it. To really stop you press BACK → OK.

There is no pause and no automatic end — a timelapse runs until you
stop it or the Pi powers down.

---

## Switching configurations on the fly (§4.2)

While one configuration is running, move the cursor to a **different**
one and press **OK**. The engine switches to it immediately but
**keeps the same grid** (`t0` is preserved): the new interval applies
from the next grid mark onward. This is the headline feature — e.g.
switching from a "partial eclipse" config to a "totality" config the
instant totality begins, without breaking the rhythm.

The footer changes to `OK switch here` when the cursor is on a config
other than the running one.

> If you switch during a multi-shot bracket, the change takes effect
> **after** that bracket finishes (§4.5).

---

## Auto mode, manual mode, and brackets (§2, §3)

Each configuration is one of two modes, decided by how many shots it
has:

- **Auto** — shown as `1 shot` with the camera metering. The fp meters
  and exposes each shot itself (`ExposureMode=ProgramAuto`). Use this
  for "set interval, let the camera decide".
- **Manual** — 1 to 9 explicit **shots**. Each shot has a fixed
  **shutter**, **ISO**, and optional **aperture**, and the app pushes
  them to the camera before firing.

A manual configuration with **more than one shot** is a **bracket**:
its shots fire **back-to-back** within a single grid tick, in listed
order. This is how you cover the enormous dynamic range of a solar
eclipse — several exposures per interval.

### Aperture and `f/—`

Aperture may be a real value (`f/5.6`) or **`f/—`** ("none"). `f/—`
means the app does not drive the aperture — the physical setting on the
lens is used. This is the normal choice for manually adapted lenses
that don't report aperture electronically.

---

## Creating and editing a configuration (§7.3)

To **create**: select `+ New configuration` and press OK.
To **edit** an existing one: long-press OK on it and choose **Edit**.

```
┌────────────────────────────────────────┐
│ EDIT · Totality                        │
├────────────────────────────────────────┤
│   name                Totality         │
│   interval                5 s          │
│   shots                   5            │
│ ─────────────────────────────────────  │
│▌ #1 shutter            1/500           │ ← cursor on a field
│  #1 iso                 400            │
│  #1 aperture             —             │
│  …                                     │
├────────────────────────────────────────┤
│ ↑↓ field   ←→ value   OK save   BACK   │
└────────────────────────────────────────┘
```

- `↑` / `↓` move between fields.
- `←` / `→` change the value of the current field (left = previous,
  right = next, from a fixed list):
  - **interval** cycles `1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 300,
    600` seconds.
  - **shots** cycles `1 (auto), 1, 2, …, 9`. Picking `1 (auto)` makes
    the config auto mode; the per-shot rows disappear. Going back to a
    number restores the manual shots you had (a newly grown shot copies
    the previous one's values).
  - **#N shutter** cycles the fp's shutter speeds (`1/8000 … 30 s`).
  - **#N iso** cycles full stops (`100, 200, …, 25600`).
  - **#N aperture** cycles `—` (none), then `1.4 … 22`.
- **OK** opens a `Save changes?` confirmation. Confirm with OK.
- **BACK** leaves edit. If you changed anything it first asks
  `Discard changes?`; if nothing changed it returns straight to the
  list.

> **Names can't be typed on the device** (§7.6). The `name` field is
> shown but read-only — see *Naming* below.

---

## The manage menu (long-press OK) (§7.5)

Long-press **OK** (≥ 3 s) on any existing configuration to open it.
It works whether or not a timelapse is running, and never disturbs a
running timelapse.

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

- **Edit** — open the edit screen.
- **Duplicate** — make a copy named `<name> (copy)`, placed right
  after the original and selected.
- **Delete** — asks to confirm, then removes it. Deleting the
  *running* config also stops the timelapse (the same confirmation
  covers both).
- **Cancel** — close the menu.

`↑` / `↓` move, `OK` selects, `BACK` closes without doing anything.

---

## Naming configurations (§7.6)

Because typing with six buttons is impractical, names are **assigned
automatically**:

- `+ New configuration` creates `Config 1`, `Config 2`, … (next free
  number).
- **Duplicate** appends `(copy)` (then `(copy 2)`, …).

To give a configuration a meaningful name, edit the JSON file directly
(see below) and restart the app.

---

## Status messages and banners

| What you see            | Meaning                                                            |
|-------------------------|--------------------------------------------------------------------|
| `fp ●` (green)          | Camera connected and responding.                                   |
| `fp ✕` (red)            | Camera not connected. The app keeps retrying automatically; you can still navigate and edit, but starting a run reports an error. |
| `SKIPS N`               | N grid ticks were missed because a bracket was still firing when the next tick arrived (§5.2). The grid stays in sync; this is informational. |
| `CAMERA NOT RESPONDING` | Banner after 5 consecutive failed shots. It clears automatically as soon as one shot succeeds (§6.1). |
| `CONFIGS RESET`         | Banner shown when the configurations file was unreadable at startup and was set aside. Create or edit a configuration to clear it (§6.3). |

> The camera's **battery level is not shown** — the underlying library
> doesn't expose it. Check the battery on the camera itself (§7.1).

---

## Editing the configurations by hand (§8)

All configurations live in a single JSON file on the Pi at
`~/fp-lapse/runtime/configs.json`. You can edit it over SSH — this is
the way to rename configs or set exact values quickly:

```bash
ssh pi3 vim ~/fp-lapse/runtime/configs.json
ssh pi3 sudo systemctl restart fp-lapse
```

The app reads the file **only at startup**, so restart it for changes
to take effect. Writes from the device are atomic and keep a
`configs.json.bak` rollback copy. For the exact schema and the accepted
shutter / ISO / aperture forms, see
[reference §3.2](reference.md#32-schema).

---

## Limits (§7.7)

- Up to **20** configurations.
- Up to **9** shots per bracket.
- Names up to **20** characters.

When a limit is reached the UI tells you (a dimmed `+ New
configuration`, a brief overlay, or a value that simply stops
increasing) rather than failing silently.

---

For anything this guide leaves implicit — precise tie-breaking on the
grid, what resets `t0`, persistence and corruption rescue, the full
button matrix in every state — the [functional reference](reference.md)
is the authority.
