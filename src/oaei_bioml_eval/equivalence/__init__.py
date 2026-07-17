"""
Track 1 equivalence scoring (the organiser's half of the common scorer.

Subtask 1 - global alignment: set-based Precision/Recall/F1 over the complete 
many:many reference (no null-reference exclusion).

Subtask 2 — local ranking: MRR + Hits@k, one ranking query per reference mapping. 
The metric core is pure + space-agnostic; the loaders read OAEI RDF or alignment
TSV (rdflib, behind the `[rdf]` extra). Official reasoner-based coherence is a
separate native shared-view surface.
"""

from .metrics import (
    DEFAULT_HITS_KS,
    aggregate_across_tasks,
    global_prf1,
    global_prf1_coherence_aware,
    local_ranking_metrics,
    macro_average_across_tasks,
    micro_average_across_tasks,
    rank_by_score,
)

__all__ = [
    "DEFAULT_HITS_KS",
    "aggregate_across_tasks",
    "global_prf1",
    "global_prf1_coherence_aware",
    "local_ranking_metrics",
    "macro_average_across_tasks",
    "micro_average_across_tasks",
    "rank_by_score",
]
