"""Shared types for the multi-agent architecture."""

from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """Standard result envelope returned by every agent."""

    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class EvalResult:
    """Standard result from eval agents."""

    passed: bool
    dimension: str  # "correctness" | "efficiency" | "quality"
    details: str = ""
    data: dict = field(default_factory=dict)
