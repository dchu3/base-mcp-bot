"""Logging configuration utilities."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import structlog


def configure_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    console: bool = True,
) -> None:
    """Configure stdlib logging + structlog for structured output.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to write logs to file.
        console: Whether to output logs to console (default True).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Add console handler if enabled
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(console_handler)

    # Add file handler if specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="w")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(file_handler)

    # Determine output target for structlog
    if log_file and not console:
        # File only - use file logger factory
        logger_factory = structlog.WriteLoggerFactory(file=log_file.open("a"))
    else:
        # Console (with or without file)
        logger_factory = structlog.PrintLoggerFactory()

    structlog.configure(
        processors=[
            structlog.threadlocal.merge_threadlocal_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        context_class=dict,
        logger_factory=logger_factory,
        cache_logger_on_first_use=False,  # Allow reconfiguration
    )


def get_logger(name: str) -> "structlog.stdlib.BoundLogger":
    """Return a structlog logger with consistent defaults."""
    return structlog.get_logger(name)


def bind_context(**kwargs: Dict[str, Any]) -> None:
    """Bind contextual information to the current log context."""
    structlog.threadlocal.bind_threadlocal(**kwargs)
