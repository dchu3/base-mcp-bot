"""JSON parsing utilities for LLM responses."""

import json
from typing import Any, Dict


def parse_llm_json(text: str) -> Dict[str, Any]:
    """
    Parse JSON from LLM response, handling markdown code blocks.

    Args:
        text: Raw text from LLM that may contain JSON wrapped in markdown.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        json.JSONDecodeError: If the text cannot be parsed as JSON.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Robust cleanup for markdown code blocks
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        return json.loads(cleaned)
