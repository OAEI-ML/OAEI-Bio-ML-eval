"""
oaei_bioml_eval.coherence.loaders: read the inputs the coherence orchestrator needs.

The global `=` set is reused verbatim from the equivalence loader (OAEI RDF or
DeepOnto TSV -> `(src, tgt)` pairs; rdflib behind the `[rdf]` extra). The local
committed mapping is each query's RANK-1 target from the matcher's own
`local.test.ranked.tsv` — the matcher's committed decision, NOT the gold — in the
`SrcEntity, TgtCandidates` list form the baselines runner writes (ranked IRI pool).
"""
from __future__ import annotations

from pathlib import Path

from ..io import parse_list, read_tsv
from ..equivalence.loaders import load_global_pairs   # the `=` set; re-exported

__all__ = ["load_global_pairs", "load_committed_top1"]


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
