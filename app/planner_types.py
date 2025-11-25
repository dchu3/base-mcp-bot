"""Shared types for the planner system."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class PlannerResult:
    """Rendered planner response plus normalized token context."""

    message: str
    tokens: List[Dict[str, str]]
