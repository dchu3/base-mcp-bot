"""Logging configuration utilities."""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib logging + structlog for structured output."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

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
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> "structlog.stdlib.BoundLogger":
    """Return a structlog logger with consistent defaults."""
    return structlog.get_logger(name)


def bind_context(**kwargs: Dict[str, Any]) -> None:
    """Bind contextual information to the current log context."""
    structlog.threadlocal.bind_threadlocal(**kwargs)
