from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
import structlog

_CONFIGURED = False
_LOG_FILE: Optional[Path] = None


def configure(level: str = "INFO", log_dir: Path = Path("logs")) -> None:
    """Configure structlog → stdlib logging with both stdout and a per-run file.

    Idempotent across the process: the first call wins, subsequent calls are
    no-ops. This matters because `run` calls each subcommand body which each
    calls configure(); without the guard we'd reset handlers mid-pipeline.
    """
    global _CONFIGURED, _LOG_FILE
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _LOG_FILE = log_dir / f"run_{ts}.log"

    log_level = getattr(logging, level.upper())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    fmt = logging.Formatter("%(message)s")
    for h in (logging.FileHandler(_LOG_FILE, encoding="utf-8"), logging.StreamHandler()):
        h.setLevel(log_level)
        h.setFormatter(fmt)
        root.addHandler(h)

    # Route structlog through stdlib so both handlers (file + stream) receive
    # every event. Without LoggerFactory(), structlog defaults to print-to-stdout
    # and bypasses stdlib entirely — which is why earlier `logs/run_*.log` files
    # were empty despite the FileHandler being attached.
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None):
    return structlog.get_logger(name)


def log_file_path() -> Optional[Path]:
    return _LOG_FILE
