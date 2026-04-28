"""Root logger configuration: stderr + rotating file under XDG state dir."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.paths import state_dir

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str) -> None:
    log_file = state_dir() / "vexis.log"

    root = logging.getLogger()
    root.setLevel(level)
    # Clear handlers in case setup_logging is called twice (tests, reload).
    root.handlers.clear()

    formatter = logging.Formatter(_FMT)

    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    root.addHandler(stderr)

    rotating = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    root.addHandler(rotating)
