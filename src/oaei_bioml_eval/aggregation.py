"""Shared aggregation primitives for cross-task evaluation reports."""

from __future__ import annotations

from typing import Literal


AverageMode = Literal["micro", "macro"]
AVERAGE_MODES: tuple[AverageMode, ...] = ("micro", "macro")


def validate_average(value: str) -> AverageMode:
    """Return a supported averaging mode or raise a stable, actionable error."""
    if value not in AVERAGE_MODES:
        choices = ", ".join(AVERAGE_MODES)
        raise ValueError(f"average must be one of {choices}; got {value!r}")
    return value  # type: ignore[return-value]


def weighted_mean(weighted_values: list[tuple[float, float]]) -> float:
    """Return ``sum(value * weight) / sum(weight)`` with an empty result of zero."""
    denominator = sum(weight for _value, weight in weighted_values)
    if denominator <= 0.0:
        return 0.0
    return sum(value * weight for value, weight in weighted_values) / denominator

