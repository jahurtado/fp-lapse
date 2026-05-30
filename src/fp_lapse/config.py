"""Static config: paths, defaults, hardware constants."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
RUNTIME_DIR: Path = PROJECT_ROOT / "runtime"
CONFIGS_FILE: Path = RUNTIME_DIR / "configs.json"
LOG_FILE: Path = RUNTIME_DIR / "fp-lapse.log"
SCHEDULE_STATE_FILE: Path = RUNTIME_DIR / "schedule_state.json"
