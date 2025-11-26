"""JSON parsing utilities for LLM responses."""

import json
import re
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
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to fix common LLM JSON mistakes
            fixed = _fix_common_json_errors(cleaned)
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e:
                # Include cleaned text preview in error for debugging
                preview = cleaned[:100] + "..." if len(cleaned) > 100 else cleaned
                raise json.JSONDecodeError(
                    f"Failed to parse LLM JSON. Preview: {preview}", e.doc, e.pos
                ) from e


def _fix_common_json_errors(text: str) -> str:
    """Attempt to fix common JSON errors from LLM output.

    Handles:
    - Unescaped quotes inside string values
    - Single quotes instead of double quotes
    """

    # Try to fix unescaped quotes in string values
    # This regex finds string values and escapes internal quotes
    # Pattern: match content between "key": " and the closing "
    def escape_inner_quotes(match):
        content = match.group(1)
        # Escape any unescaped quotes inside the string
        escaped = content.replace('\\"', "__ESCAPED_QUOTE__")
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("__ESCAPED_QUOTE__", '\\"')
        return f'"{escaped}"'

    # Replace single quotes with double quotes for keys and simple values
    text = re.sub(r"'(\w+)'(\s*:)", r'"\1"\2', text)

    # Try to find and fix unescaped quotes in values
    # This is a best-effort fix for patterns like "key": "value with "quotes" inside"
    # Look for patterns where we have ": " followed by content with unbalanced quotes
    try:
        # Simple approach: try to parse, if it fails at a specific position,
        # that might indicate an unescaped quote
        json.loads(text)
        return text
    except json.JSONDecodeError as e:
        # Try to fix at the error position
        if e.pos and e.pos < len(text):
            # Check if there's an unescaped quote nearby
            start = max(0, e.pos - 50)
            end = min(len(text), e.pos + 50)
            snippet = text[start:end]

            # If we see a pattern like `"word "word"` try to escape the inner quote
            # This is a heuristic fix
            if '"' in snippet:
                # Replace the problematic quote with escaped version
                fixed = text[: e.pos] + "\\" + text[e.pos :]
                return fixed

    return text
