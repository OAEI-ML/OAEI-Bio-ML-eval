"""
oaei_bioml_eval.equivalence.loaders: read Track 1 submissions + gold off disk.

Global alignment comes as OAEI Alignment RDF or a DeepOnto `(SrcEntity, TgtEntity
[, Score])` TSV, both reduced to a set of `(src, tgt)` pairs. Local ranking comes
as either a per-query `TgtCandidates` list (pre-ranked) or a scored block TSV
(`SrcEntity, TgtEntity, Score`, `candidate_count` rows/query, positional query
order); the gold is one `(src, tgt)` per query in that same order.

RDF parsing uses rdflib (the `[rdf]` extra) — imported lazily so the pure metric
core and the TSV path stay dependency-free.
"""
from __future__ import annotations

from pathlib import Path

from ..io import parse_list, read_tsv
from .metrics import rank_by_score

_RDF_SUFFIXES = {".rdf", ".xml", ".owl", ".ttl", ".n3"}
_ALIGN_NS = "http://knowledgeweb.semanticweb.org/heterogeneity/alignment#"
_EQUIVALENCE_RELATIONS = {"=", "equivalent", "equiv"}


def load_pairs_tsv(path: str | Path) -> set[tuple[str, str]]:
    """`(SrcEntity, TgtEntity)` pairs from a DeepOnto-style alignment TSV"""
    return {(row["SrcEntity"], row["TgtEntity"]) for row in read_tsv(path)}


def load_pairs_rdf(path: str | Path) -> set[tuple[str, str]]:
    """`(entity1, entity2)` pairs from an OAEI Alignment-API RDF (equivalence cells)"""
    from rdflib import Graph, URIRef    # type: ignore
    from rdflib.namespace import RDF    # type: ignore

    align = lambda local: URIRef(_ALIGN_NS + local)   # noqa: E731 — terse local alias
    graph = Graph()
    graph.parse(str(path))
    cells = set(graph.subjects(RDF.type, align("Cell"))) or set(graph.subjects(align("entity1"), None))
    pairs: set[tuple[str, str]] = set()
    for cell in cells:
        entity1 = next(graph.objects(cell, align("entity1")), None)
        entity2 = next(graph.objects(cell, align("entity2")), None)
        relation = next(graph.objects(cell, align("relation")), None)
        if entity1 is None or entity2 is None:
            continue
        relation_text = "" if relation is None else str(relation).strip()
        if relation_text == "" or relation_text in _EQUIVALENCE_RELATIONS:   # absent/empty -> equivalence
            pairs.add((str(entity1), str(entity2)))
    return pairs


def load_global_pairs(path: str | Path) -> set[tuple[str, str]]:
    """a global alignment (submission OR reference) -> `(src, tgt)` pairs; RDF or TSV by suffix"""
    return load_pairs_rdf(path) if Path(path).suffix.lower() in _RDF_SUFFIXES else load_pairs_tsv(path)


def load_local_gold(path: str | Path) -> list[tuple[str, str]]:
    """one `(src, gold_target)` per ranking query, in the canonical row order"""
    return [(row["SrcEntity"], row["TgtEntity"]) for row in read_tsv(path)]


def load_local_ranking(path: str | Path, candidate_count: int) -> list[tuple[str, list[str]]]:
    """
    per-query `(src, ranked_targets)` in the canonical query order. accepts either a
    `TgtCandidates` list cell per query (the list IS the ranking) or a scored block
    (`candidate_count` `(TgtEntity, Score)` rows per query, sorted best-first). the
    block form is validated SrcEntity-homogeneous + full-length so a ragged/misaligned
    block can't silently corrupt every later query's ranking.
    """
    rows = read_tsv(path)
    if rows and "TgtCandidates" in rows[0]:
        return [(row["SrcEntity"], parse_list(row["TgtCandidates"])) for row in rows]
    rankings: list[tuple[str, list[str]]] = []
    for start in range(0, len(rows), candidate_count):
        block = rows[start:start + candidate_count]
        sources = {row["SrcEntity"] for row in block}
        if len(sources) != 1:
            raise ValueError(f"local ranking block at row {start} spans multiple sources {sorted(sources)}; "
                             f"each query must be {candidate_count} consecutive rows for one source.")
        if len(block) != candidate_count:
            raise ValueError(f"local ranking block at row {start} has {len(block)} rows, expected {candidate_count}.")
        source = block[0]["SrcEntity"]
        if block[0].get("Score", "") != "":
            ranked = rank_by_score([(row["TgtEntity"], float(row["Score"])) for row in block])
        else:
            ranked = [row["TgtEntity"] for row in block]
        rankings.append((source, ranked))
    return rankings
