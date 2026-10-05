"""Per-request reasoning choices; shared model/profile objects remain unchanged."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

GenerationTask = Literal[
    "intent",
    "context_resolution",
    "search_query",
    "extraction",
    "verification",
    "summary",
    "formatting",
    "research_planning",
    "scientific_synthesis",
    "complex_derivation",
    "formula_verification",
]


class GenerationOptions(TypedDict, total=False):
    reasoning_effort: str


_LOW_REASONING_TASKS: frozenset[GenerationTask] = frozenset(
    {
        "intent",
        "context_resolution",
        "search_query",
        "extraction",
        "verification",
        "summary",
        "formatting",
    }
)


def generation_options(llm: Any, task: GenerationTask) -> GenerationOptions:
    """Bounded transformations use low effort only for declared reasoning models.

    Scientific synthesis, research planning, derivations and formula validation
    keep the configured effort. Callers choose a task from its output contract,
    not from the role's name or model name. Each call receives a fresh mapping.
    """
    if getattr(llm, "parameter_mode", None) == "reasoning" and task in _LOW_REASONING_TASKS:
        return {"reasoning_effort": "low"}
    return {}
