"""Timelapse configurations: data model, validation, persistence.

A `TimelapseConfig` bundles a name, interval, and a non-empty list of
`Shot`s. The whole list lives on disk as a single JSON file at
`runtime/configs.json`, is loaded at startup, and rewritten atomically
whenever the user edits / creates / duplicates / deletes. See
docs/reference.md §3 (data model) and §8 (persistence).

Two levels of validation:

- **Schema** (applies when loading JSON): types must match and the
  shape must be respected. A schema violation falls under §6.3 →
  "corrupt file", the file is moved aside as `.bak.<timestamp>` and
  the app starts empty.
- **Hard limits** (§7.7) at load time: count overruns (more than 20
  configs, more than 9 shots, name > 20 chars) are **NOT** corruption —
  truncate + WARNING.
- **Strict validation** at save time: any schema, hard-limit or range
  violation raises `ConfigValidationError`. The edit UI relies on this
  to refuse the save (§6.2).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .shutter import format_shutter, in_range as shutter_in_range, parse_shutter

logger = logging.getLogger(__name__)

# Hard limits (§7.7 of docs/reference.md)
MAX_CONFIGS: int = 20
MAX_SHOTS_PER_BRACKET: int = 9
MAX_NAME_LENGTH: int = 20

# ISO range accepted by the JSON validator. Matches the fp's native
# range (100..25600). The UI cycling (`ISO_VALUES` in
# `ui/edit_values.py`) only offers full native stops (`100, 200, 400,
# …`); this range is wider so external JSON edits can use 1/3 EV
# intermediates if needed.
ISO_MIN: int = 100
ISO_MAX: int = 25600

SCHEMA_VERSION: int = 1


class ConfigsError(Exception):
    """Base class for configs-module errors."""


class ConfigSchemaError(ConfigsError):
    """The JSON does not match the expected schema (§6.3 → corruption)."""


class ConfigValidationError(ConfigsError):
    """A config violates a strict validation rule (§6.2 / §7.7)."""


@dataclass(frozen=True)
class Shot:
    """One exposure in a manual bracket.

    `shutter` and `iso` are always explicit. `aperture` may be `None`
    (manual lens with no electronic aperture control — the camera
    uses whatever ring position the lens is set to).
    """
    shutter: float
    iso: int
    aperture: Optional[float] = None

    def format_shutter(self) -> str:
        return format_shutter(self.shutter)

    def format_iso(self) -> str:
        return f"ISO {self.iso}"

    def format_aperture(self) -> str:
        if self.aperture is None:
            return "f/—"
        v = float(self.aperture)
        if v == int(v):
            return f"f/{int(v)}"
        return f"f/{v:.1f}"


@dataclass(frozen=True)
class TimelapseConfig:
    """A timelapse config.

    `shots == ()` is **auto mode**: 1 shot per interval, exposure
    metered by the camera (ProgramAuto). The UI displays this as
    `Shots: 1 (auto)`.

    `shots` with 1..9 items is **manual mode**: one explicit shot per
    bracket position, exposure values come from each `Shot`.
    """
    name: str
    interval_s: float
    shots: tuple[Shot, ...]

    @property
    def is_auto(self) -> bool:
        return len(self.shots) == 0

    @property
    def shots_per_interval(self) -> int:
        """How many shots the engine fires at each grid mark."""
        return 1 if self.is_auto else len(self.shots)


def _parse_iso(raw) -> int:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigSchemaError(f"iso must be an int, got {raw!r}")
    return raw


def _parse_aperture(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ConfigSchemaError(f"invalid aperture value: {raw!r}")
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v <= 0:
            raise ConfigSchemaError(f"aperture must be > 0: {raw!r}")
        return v
    raise ConfigSchemaError(f"invalid aperture type: {type(raw).__name__}")


def _parse_shot(raw) -> Shot:
    if not isinstance(raw, dict):
        raise ConfigSchemaError(f"shot must be an object, got {type(raw).__name__}")
    try:
        shutter = parse_shutter(raw.get("shutter"))
    except ValueError as e:
        raise ConfigSchemaError(f"invalid shutter: {e}") from None
    iso = _parse_iso(raw.get("iso"))
    aperture = _parse_aperture(raw.get("aperture"))
    return Shot(shutter=shutter, iso=iso, aperture=aperture)


def _parse_config(raw) -> TimelapseConfig:
    if not isinstance(raw, dict):
        raise ConfigSchemaError(f"config must be an object, got {type(raw).__name__}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigSchemaError(f"config name must be a non-empty string, got {name!r}")
    if len(name) > MAX_NAME_LENGTH:
        truncated = name[:MAX_NAME_LENGTH]
        logger.warning(
            "config name %r exceeds %d chars, truncated to %r",
            name, MAX_NAME_LENGTH, truncated,
        )
        name = truncated
    interval = raw.get("interval_s")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ConfigSchemaError(f"interval_s must be a number, got {interval!r}")
    if interval <= 0:
        raise ConfigSchemaError(f"interval_s must be > 0, got {interval!r}")
    shots_raw = raw.get("shots")
    if not isinstance(shots_raw, list):
        raise ConfigSchemaError("shots must be a list (possibly empty for auto mode)")
    shots = [_parse_shot(s) for s in shots_raw]
    if len(shots) > MAX_SHOTS_PER_BRACKET:
        logger.warning(
            "config %r has %d shots, truncated to %d",
            name, len(shots), MAX_SHOTS_PER_BRACKET,
        )
        shots = shots[:MAX_SHOTS_PER_BRACKET]
    return TimelapseConfig(name=name, interval_s=float(interval), shots=tuple(shots))


def _shot_to_dict(shot: Shot) -> dict:
    return {"shutter": shot.shutter, "iso": shot.iso, "aperture": shot.aperture}


def _config_to_dict(cfg: TimelapseConfig) -> dict:
    return {
        "name": cfg.name,
        "interval_s": cfg.interval_s,
        "shots": [_shot_to_dict(s) for s in cfg.shots],
    }


def validate_strict(configs: Iterable[TimelapseConfig]) -> None:
    """Enforce hard limits, name uniqueness, and value ranges. Raises
    `ConfigValidationError` on any violation."""
    configs = list(configs)
    if len(configs) > MAX_CONFIGS:
        raise ConfigValidationError(
            f"too many configs: {len(configs)} > {MAX_CONFIGS}"
        )
    seen: set[str] = set()
    for c in configs:
        if not c.name or len(c.name) > MAX_NAME_LENGTH:
            raise ConfigValidationError(f"name length out of range: {c.name!r}")
        if c.name in seen:
            raise ConfigValidationError(f"duplicate config name: {c.name!r}")
        seen.add(c.name)
        if c.interval_s <= 0:
            raise ConfigValidationError(f"interval_s must be > 0 in {c.name!r}")
        if len(c.shots) > MAX_SHOTS_PER_BRACKET:
            raise ConfigValidationError(
                f"too many shots in {c.name!r}: {len(c.shots)}"
            )
        # `shots == ()` is the legal "auto mode" sentinel — skip per-shot
        # range checks.
        for i, s in enumerate(c.shots, start=1):
            if not shutter_in_range(s.shutter):
                raise ConfigValidationError(
                    f"shutter out of range in {c.name!r}[shot {i}]: {s.shutter}"
                )
            if not (ISO_MIN <= s.iso <= ISO_MAX):
                raise ConfigValidationError(
                    f"iso out of range in {c.name!r}[shot {i}]: {s.iso}"
                )


class ConfigStore:
    """Persistence for the config list (§8).

    - Atomic write: `.tmp` + rename. Before every write the previous
      file is copied to `<path>.bak` (single-slot rotation, no
      timestamp).
    - Corruption rescue on load (§6.3): a JSON file that doesn't parse
      or violates the schema is renamed to
      `<path>.bak.<YYYYMMDD-HHMMSS>` and the store starts empty;
      `was_reset_from_corruption` becomes True so the UI can surface
      the `CONFIGS RESET` banner + long buzzer beep.
    - If the file does not exist at load time it is initialized empty
      on disk.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        self.was_reset_from_corruption: bool = False

    def load(self) -> list[TimelapseConfig]:
        self.was_reset_from_corruption = False
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write({"version": SCHEMA_VERSION, "configs": []})
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return self._parse_root(data)
        except (json.JSONDecodeError, ConfigSchemaError) as e:
            logger.error("configs.json is corrupt (%s); rescuing", e)
            self._rescue_corrupt_file()
            self.was_reset_from_corruption = True
            return []

    def save(self, configs: list[TimelapseConfig]) -> None:
        validate_strict(configs)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                self.backup_path.write_bytes(self.path.read_bytes())
            except OSError as e:
                logger.warning("could not rotate backup for %s: %s", self.path, e)
        payload = {
            "version": SCHEMA_VERSION,
            "configs": [_config_to_dict(c) for c in configs],
        }
        self._atomic_write(payload)

    def _atomic_write(self, payload: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def _parse_root(self, data) -> list[TimelapseConfig]:
        if not isinstance(data, dict):
            raise ConfigSchemaError("root must be an object")
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise ConfigSchemaError(
                f"unsupported version: {version!r} (expected {SCHEMA_VERSION})"
            )
        configs_raw = data.get("configs")
        if not isinstance(configs_raw, list):
            raise ConfigSchemaError("`configs` must be a list")
        configs = [_parse_config(c) for c in configs_raw]
        if len(configs) > MAX_CONFIGS:
            logger.warning(
                "found %d configs, truncated to %d", len(configs), MAX_CONFIGS
            )
            configs = configs[:MAX_CONFIGS]
        return configs

    def _rescue_corrupt_file(self) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rescued = self.path.parent / f"{self.path.name}.bak.{ts}"
        try:
            os.replace(self.path, rescued)
            logger.error("moved corrupt configs file to %s", rescued)
        except OSError as e:
            logger.error("could not rename corrupt configs file: %s", e)
