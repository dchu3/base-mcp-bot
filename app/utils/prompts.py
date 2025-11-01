"""Helpers for loading prompt templates."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


def load_prompt_template(path: Optional[Path]) -> Optional[str]:
    """Return prompt template contents from ``path`` if provided."""
    if path is None:
        return None

    try:
        content = path.read_text(encoding="utf-8")
        return content.strip()
    except FileNotFoundError:
        logger.warning("prompt_template_missing", path=str(path))
    except OSError as exc:  # pragma: no cover - filesystem issues
        logger.error("prompt_template_error", path=str(path), error=str(exc))
    return None


__all__ = ["load_prompt_template"]
