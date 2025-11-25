"""Shared types for the planner system."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class PlannerResult:
    """Rendered planner response plus normalized token context.

    Attributes:
        message: The formatted response text to send to the user.
            Should be escaped for Telegram MarkdownV2 if using markdown.
        tokens: List of token metadata dicts discovered during planning.
            Each dict may contain keys like 'address', 'symbol', 'name'.
    """

    message: str
    tokens: List[Dict[str, str]]
