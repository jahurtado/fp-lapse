"""Root logger config: console + rotating file under runtime/."""

from __future__ import annotations

import logging
import logging.handlers

from .config import LOG_FILE, RUNTIME_DIR


def setup_logging(level: int = logging.INFO) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3
        ),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
