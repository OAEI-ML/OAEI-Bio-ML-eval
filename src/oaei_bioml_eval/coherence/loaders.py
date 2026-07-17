"""
oaei_bioml_eval.coherence.loaders: read the inputs the coherence orchestrator needs.

The global `=` set is reused verbatim from the equivalence loader (OAEI RDF or
alignment TSV -> `(src, tgt)` pairs; rdflib behind the `[rdf]` extra). The local
committed mapping is each query's RANK-1 target from the matcher's own
`local.test.ranked.tsv` — the matcher's committed decision, NOT the gold — in the
`SrcEntity, TgtCandidates` list form the baselines runner writes (ranked IRI pool).
"""
from __future__ import annotations

from pathlib import Path

from ..io import parse_list, read_tsv
from ..equivalence.loaders import load_global_pairs   # the `=` set; re-exported

__all__ = ["load_global_pairs", "load_committed_top1", "load_relation_typed_correspondences"]

_RDF_SUFFIXES = {".rdf", ".xml", ".owl", ".ttl", ".n3"}
_ALIGN_NS = "http://knowledgeweb.semanticweb.org/heterogeneity/alignment#"
# canonicalise an OAEI relation to the bridge's vocabulary; '?' / unknown -> None (NOT asserted)
_REL_CANON = {"": "=", "=": "=", "equivalent": "=", "equiv": "=",
              "<": "<=", "<=": "<=", ">": ">=", ">=": ">="}


def load_relation_typed_correspondences(path: str | Path) -> list[tuple[str, str, str]]:
    """
    an Option-Two reference's KEPT correspondences as `(src, tgt, relation)` triples for the
    relation-aware coherence bridge: `=`/equiv/absent -> `=`; `<`/`<=` -> `<=`; `>`/`>=` -> `>=`.
    `?` (LargeBio-flagged) and any unrecognised relation are DROPPED — they are not asserted in
    the repaired reference. RDF by suffix; otherwise a TSV with a `Relation` column (default `=`).
    """
    if Path(path).suffix.lower() not in _RDF_SUFFIXES:
        out: list[tuple[str, str, str]] = []
        for row in read_tsv(path):
            canon = _REL_CANON.get((row.get("Relation") or "=").strip())
            if canon is not None:
                out.append((row["SrcEntity"], row["TgtEntity"], canon))
        return out
    from rdflib import Graph, URIRef    # type: ignore
    from rdflib.namespace import RDF    # type: ignore

    align = lambda local: URIRef(_ALIGN_NS + local)   # noqa: E731 — terse local alias
    graph = Graph()
    graph.parse(str(path))
    cells = set(graph.subjects(RDF.type, align("Cell"))) | set(graph.subjects(align("entity1"), None))
    out = []
    for cell in cells:
        entity1 = next(graph.objects(cell, align("entity1")), None)
        entity2 = next(graph.objects(cell, align("entity2")), None)
        relation = next(graph.objects(cell, align("relation")), None)
        if entity1 is None or entity2 is None:
            continue
        canon = _REL_CANON.get(("" if relation is None else str(relation).strip()))
        if canon is not None:   # drop '?' / unknown
            out.append((str(entity1), str(entity2), canon))
    return out


def load_committed_top1(ranked_path: str | Path) -> list[tuple[str, str]]:
    """
    per ranking query, its committed `(src, rank-1 target)` from a ranked-pool TSV.
    queries whose pool is empty commit nothing and are dropped (no mapping to blame);
    duplicates across View-B per-mapping queries are kept (the bridge dedups them).
    """
    committed: list[tuple[str, str]] = []
    for row in read_tsv(ranked_path):
        pool = parse_list(row.get("TgtCandidates", ""))
        if pool:
            committed.append((row["SrcEntity"], pool[0]))
    return committed
