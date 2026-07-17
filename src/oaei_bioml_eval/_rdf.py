"""Typed lazy boundary around RDFLib's partially typed graph API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, cast


class RdfGraph(Protocol):
    def parse(self, source: str) -> object: ...

    def subjects(
        self, predicate: object, object_: object | None = None
    ) -> Iterator[object]: ...

    def objects(self, subject: object, predicate: object) -> Iterator[object]: ...


class RdfGraphFactory(Protocol):
    def __call__(self) -> RdfGraph: ...


class UriFactory(Protocol):
    def __call__(self, value: str) -> object: ...


class RdfNamespace(Protocol):
    type: object


def rdf_api() -> tuple[RdfGraphFactory, UriFactory, RdfNamespace]:
    """Import RDFLib only when an RDF input is actually requested."""

    from rdflib import Graph, URIRef
    from rdflib.namespace import RDF

    return (
        cast(RdfGraphFactory, Graph),
        cast(UriFactory, URIRef),
        cast(RdfNamespace, RDF),
    )
