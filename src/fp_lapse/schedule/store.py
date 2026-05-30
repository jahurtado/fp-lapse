"""`ScheduleStateStore` — persists the global schedule on/off flag.

Tiny file: `runtime/schedule_state.json` holds one boolean. Kept
separate from `configs.json` so each concern stays minimal and
independently recoverable.

Atomic-write idiom copied (deliberately, not reused) from
`ConfigStore`: write to `.tmp`, then `os.replace`. The payload is one
boolean so no rotating `.bak` is kept — rollback has no value. A
corrupt file is moved aside as `schedule_state.json.bak.<TIMESTAMP>`
and a fresh default is written.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1
DEFAULT_ENABLED: bool = False


class ScheduleStateStore:
    """Persistence for the single `schedule_enabled` boolean."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.was_reset_from_corruption: bool = False

    def load(self) -> bool:
        """Read the persisted flag. On corruption, rescue + return default."""
        self.was_reset_from_corruption = False
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(DEFAULT_ENABLED)
            return DEFAULT_ENABLED
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return self._parse(data)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.error("schedule_state.json is corrupt (%s); rescuing", e)
            self._rescue_corrupt_file()
            self.was_reset_from_corruption = True
            self._atomic_write(DEFAULT_ENABLED)
            return DEFAULT_ENABLED

    def save(self, enabled: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(bool(enabled))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _atomic_write(self, enabled: bool) -> None:
        payload = {"version": SCHEMA_VERSION, "schedule_enabled": enabled}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            os.replace(tmp, self.path)
        except OSError:
            # The tmp file may have been left behind — try to clean it up,
            # but the original is unchanged so the failure is recoverable.
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _parse(self, data: Any) -> bool:
        if not isinstance(data, dict):
            raise ValueError(f"root must be an object, got {type(data).__name__}")
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported version: {version!r}")
        enabled = data.get("schedule_enabled")
        if not isinstance(enabled, bool):
            raise ValueError(
                f"schedule_enabled must be a bool, got {type(enabled).__name__}"
            )
        return enabled

    def _rescue_corrupt_file(self) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rescued = self.path.parent / f"{self.path.name}.bak.{ts}"
        try:
            os.replace(self.path, rescued)
            logger.error("moved corrupt schedule_state file to %s", rescued)
        except OSError as e:
            logger.error("could not rename corrupt schedule_state file: %s", e)
