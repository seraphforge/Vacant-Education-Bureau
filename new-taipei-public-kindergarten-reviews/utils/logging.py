"""
Structured logging setup for the kindergarten pipeline.

Log format:
    timestamp | level | module | kindergarten | action | status | message

Usage:
    from utils.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Importing kindergartens", extra={"action": "import", "status": "start"})
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


class PipelineFormatter(logging.Formatter):
    """Custom formatter that includes pipeline-specific fields."""

    DEFAULT_FORMAT = (
        "%(asctime)s | %(levelname)-8s | %(name)s | "
        "%(kindergarten)s | %(action)s | %(status)s | %(message)s"
    )

    def format(self, record: logging.LogRecord) -> str:
        # Ensure pipeline-specific fields are present
        if not hasattr(record, "kindergarten"):
            record.kindergarten = "-"
        if not hasattr(record, "action"):
            record.action = "-"
        if not hasattr(record, "status"):
            record.status = "-"
        return super().format(record)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    console: bool = True,
) -> None:
    """
    Configure root logger with file and/or console handlers.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to write log file. If None, file logging is disabled.
        console: Whether to also log to stderr.
    """
    fmt = PipelineFormatter(PipelineFormatter.DEFAULT_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (avoid duplicate logs on re-init)
    root.handlers.clear()

    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger with the given name.

    Example::

        logger = get_logger(__name__)
        logger.info(
            "Matched place",
            extra={"action": "match", "status": "success", "kindergarten": "XX幼兒園"}
        )
    """
    return logging.getLogger(name)
