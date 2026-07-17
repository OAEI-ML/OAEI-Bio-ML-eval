"""Canonical shared-core bridge construction for coherence scoring.

This module is the only OAEI layer that constructs OWL model values.  It uses
the public ``pyowl_core`` model and zero-copy composition facade, never a private
ontology representation or serialization format.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, cast

if TYPE_CHECKING:
    from pyowl_core import (
        IRI,
        CancellationToken,
        ImportResolver,
        LoadOptions,
        OntologyComposite,
        OntologyInput,
        OntologyView,
    )
else:
    IRI = CancellationToken = ImportResolver = LoadOptions = object
    OntologyComposite = OntologyInput = OntologyView = object

Correspondence: TypeAlias = tuple[str, str] | tuple[str, str, str]
NormalizedCorrespondence: TypeAlias = tuple[str, str, str]

OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"
_TRIVIAL = frozenset({OWL_THING, OWL_NOTHING})

_RELATION_ALIASES = {
    "equivalent": "=",
    "equiv": "=",
    "<": "<=",
    ">": ">=",
}
_RELATIONS = frozenset({"=", "<=", ">="})
_BAD_IRI_CHARS = frozenset(' \t\n\r\x0b\x0c<>"{}|^`\\')


class SnapshotCompatibilityError(RuntimeError):
    """The optional shared-OWL coherence stack is absent or too old."""


@dataclass(frozen=True, slots=True)
class BridgeNormalization:
    """Canonical bridge plus deterministic, non-identifying normalization evidence."""

    correspondences: tuple[NormalizedCorrespondence, ...]
    input_count: int
    duplicate_count: int
    self_pair_count: int
    invalid_dropped_count: int = 0

    def __post_init__(self) -> None:
        if self.correspondences != tuple(sorted(set(self.correspondences))):
            raise ValueError("correspondences must be sorted and duplicate-free")
        for name in (
            "input_count",
            "duplicate_count",
            "self_pair_count",
            "invalid_dropped_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if (
            len(self.correspondences) + self.duplicate_count + self.self_pair_count
            != self.input_count
        ):
            raise ValueError("bridge normalization counts do not reconcile")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.correspondences,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(b"oaei-bioml:bridge:v1\0" + payload).hexdigest()


def coerce_ontology_pair_once(
    source: OntologyInput,
    target: OntologyInput,
    *,
    source_document_iri: IRI | str | None,
    target_document_iri: IRI | str | None,
    load_options: LoadOptions | None,
    resolver: ImportResolver | None,
    cancellation_token: CancellationToken | None,
) -> tuple[OntologyView, OntologyView]:
    """Call the core facade once for each input and retain both returned views."""

    core = _shared_core()
    source_view = core.coerce_snapshot(
        source,
        document_iri=source_document_iri,
        options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    target_view = core.coerce_snapshot(
        target,
        document_iri=target_document_iri,
        options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    return source_view, target_view


def normalize_correspondences(
    items: Iterable[Correspondence],
) -> tuple[NormalizedCorrespondence, ...]:
    """Return the canonical bridge triples for accepted relation aliases.

    Two-tuples default to equivalence.  Duplicates and self-pairs do not create
    bridge axioms.  Unsupported relations fail before composition.
    """

    return analyze_correspondences(items).correspondences


def analyze_correspondences(
    items: Iterable[Correspondence],
    *,
    invalid_dropped_count: int = 0,
) -> BridgeNormalization:
    """Normalize once while retaining the counts required by provenance."""

    if (
        isinstance(invalid_dropped_count, bool)
        or not isinstance(invalid_dropped_count, int)
        or invalid_dropped_count < 0
    ):
        raise ValueError("invalid_dropped_count must be a nonnegative integer")
    normalized: set[NormalizedCorrespondence] = set()
    input_count = 0
    self_pair_count = 0
    nonself_count = 0
    for original in items:
        input_count += 1
        item = tuple(original)
        if len(item) not in {2, 3}:
            raise ValueError(
                "bridge correspondence must contain source, target, and optional relation"
            )
        source, target = str(item[0]), str(item[1])
        relation = str(item[2]).strip().lower() if len(item) == 3 else "="
        relation = _RELATION_ALIASES.get(relation, relation)
        if relation not in _RELATIONS:
            raise ValueError(
                f"bridge correspondence ({source}, {target}) has unsupported relation "
                f"{relation!r}; expected one of =, equivalent, equiv, <=, >=, <, >."
            )
        if source == target:
            self_pair_count += 1
        else:
            nonself_count += 1
            normalized.add((source, target, relation))
    ordered = tuple(sorted(normalized))
    return BridgeNormalization(
        correspondences=ordered,
        input_count=input_count,
        duplicate_count=nonself_count - len(ordered),
        self_pair_count=self_pair_count,
        invalid_dropped_count=invalid_dropped_count,
    )


def invalid_alignment_iris(items: Iterable[Correspondence]) -> list[str]:
    """Return malformed IRI slots without interpreting relation symbols as IRIs."""

    bad = {
        iri
        for item in items
        for iri in tuple(item)[:2]
        if any(character in _BAD_IRI_CHARS or ord(character) < 0x20 for character in iri)
    }
    return sorted(bad)


def compose_alignment_views(
    source: OntologyView,
    target: OntologyView,
    correspondences: Iterable[Correspondence],
) -> OntologyComposite:
    """Build one canonical bridge delta and retain both source arenas by identity."""

    core = _shared_core()
    normalized = normalize_correspondences(correspondences)
    axioms = frozenset(_bridge_axiom(core, source_iri, target_iri, relation)
                       for source_iri, target_iri, relation in normalized)
    bridge_delta = core.OntologyDelta(add_axioms=axioms)
    merged = core.compose_views(
        source,
        target,
        delta=bridge_delta,
        roles=("source", "target"),
    )
    if not isinstance(merged, core.OntologyComposite):
        raise SnapshotCompatibilityError(
            "pyowl-core compose_views did not return OntologyComposite"
        )
    return cast(OntologyComposite, merged)


def named_class_iris(view: OntologyView) -> tuple[str, ...]:
    """Return the sorted merged named-class denominator from the shared index."""

    core = _shared_core()
    named = {
        str(entity.iri.value)
        for entity in view.signature(core.EntityKind.CLASS, include_builtins=False)
    }
    return tuple(sorted(named - _TRIVIAL))


def _bridge_axiom(core: Any, source: str, target: str, relation: str) -> object:
    source_class = core.Class(core.IRI(source))
    target_class = core.Class(core.IRI(target))
    if relation == "=":
        return core.EquivalentClasses(frozenset((source_class, target_class)))
    if relation == "<=":
        return core.SubClassOf(source_class, target_class)
    return core.SubClassOf(target_class, source_class)


def _shared_core() -> Any:
    try:
        core = importlib.import_module("pyowl_core")
    except ModuleNotFoundError as error:
        if error.name != "pyowl_core":
            raise
        raise SnapshotCompatibilityError(
            "snapshot-first coherence requires pyowl-core; install the reasoner extra"
        ) from error
    required = (
        "Class",
        "EntityKind",
        "EquivalentClasses",
        "IRI",
        "OntologyComposite",
        "OntologyDelta",
        "SubClassOf",
        "coerce_snapshot",
        "compose_views",
    )
    missing = tuple(name for name in required if not hasattr(core, name))
    if missing:
        raise SnapshotCompatibilityError(
            "installed pyowl-core lacks the O2 shared composition contract: "
            + ", ".join(missing)
        )
    return core


__all__ = [
    "BridgeNormalization",
    "Correspondence",
    "NormalizedCorrespondence",
    "SnapshotCompatibilityError",
    "analyze_correspondences",
    "coerce_ontology_pair_once",
    "compose_alignment_views",
    "invalid_alignment_iris",
    "named_class_iris",
    "normalize_correspondences",
]
