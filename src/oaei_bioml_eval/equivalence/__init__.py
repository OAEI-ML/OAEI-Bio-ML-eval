"""
Track 1 equivalence scoring (the organiser's half of the common scorer.

Subtask 1 - global alignment: set-based Precision/Recall/F1 over the complete 
many:many reference (no null-reference exclusion).

Subtask 2 — local ranking: MRR + Hits@k, one ranking query per reference mapping. 
The metric core is pure + space-agnostic; the loaders read OAEI RDF / DeepOnto TSV 
(rdflib, behind the `[rdf]` extra). The official reasoner-based coherence (DeepOnto/ELK) 
is separate + still deferred.
"""

from .metrics import (
    aggregate_across_tasks,
    DEFAULT_HITS_KS,
    global_prf1,
    global_prf1_coherence_aware,
    local_ranking_metrics,
    macro_average_across_tasks,
    micro_average_across_tasks,
    rank_by_score,
)

__all__ = [
    "aggregate_across_tasks",
    "DEFAULT_HITS_KS",
    "global_prf1",
    "global_prf1_coherence_aware",
    "local_ranking_metrics",
    "macro_average_across_tasks",
    "micro_average_across_tasks",
    "rank_by_score",
]
