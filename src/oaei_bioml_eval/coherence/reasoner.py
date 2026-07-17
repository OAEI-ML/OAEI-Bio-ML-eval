"""Native shared-view reasoner contract for official coherence scoring."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UnsatResult:
    """Sorted unsatisfiable named classes plus reproducible reasoner metadata."""

    unsatisfiable: tuple[str, ...]
    reasoner_used: str
    elapsed_seconds: float
    provenance: Mapping[str, Any] = field(default_factory=dict)
    inconsistent: bool = False
    fallback_reason: str | None = None


class CoherenceReasoner(ABC):
    """Classify one already composed shared ontology view."""

    name: str = "base"

    @abstractmethod
    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        """Classify ``ontology`` without materializing, serializing, or reparsing it."""

        raise NotImplementedError


def load_reasoner() -> CoherenceReasoner:
    """Load the Java-free native dispatcher lazily."""

    from .native_reasoners import NativeReasoner

    return NativeReasoner()


__all__ = ["CoherenceReasoner", "UnsatResult", "load_reasoner"]
