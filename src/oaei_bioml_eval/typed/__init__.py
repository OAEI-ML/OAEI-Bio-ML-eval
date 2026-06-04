"""
oaei_bioml_eval.typed: Track 2 typed-ranking metrics.

Preferred Relation-Aware (Typed) MRR and Hierarchy-Aware Typed nDCG@10, over a
fixed candidate pool with exactly one preferred (target, relation) gold per
query. Dependency-free.

The gain function + preferred-pair policy live in `relevance` (single-sourced —
the organiser's reference build imports them too); `loaders` reads the gold +
block submission, `metrics`/`diagnostic` score it, and `report`/`cli` are the
file-level entry points.
"""
from __future__ import annotations

from .diagnostic import (
    score_entity_only_metrics,
    score_relation_on_preferred_entity_metrics,
)
from .loaders import (
    load_answers,
    load_block_format_predictions,
    load_graded_relevance,
    load_per_query_candidate_sets,
    load_per_query_candidate_sets_from_answers,
    load_preferred_pairs,
)
from .metrics import (
    DEFAULT_RELATIONS,
    hierarchy_aware_ndcg,
    score_preferred_typed_metrics,
    score_typed_metrics,
)
from .relevance import (
    DEFAULT_MAX_DISTANCE,
    compute_graded_relevance,
    select_preferred_pairs,
)
from .report import macro_average_across_tasks, score_files

__all__ = [
    "DEFAULT_MAX_DISTANCE",
    "DEFAULT_RELATIONS",
    "compute_graded_relevance",
    "hierarchy_aware_ndcg",
    "load_answers",
    "load_block_format_predictions",
    "load_graded_relevance",
    "load_per_query_candidate_sets",
    "load_per_query_candidate_sets_from_answers",
    "load_preferred_pairs",
    "macro_average_across_tasks",
    "score_entity_only_metrics",
    "score_files",
    "score_preferred_typed_metrics",
    "score_relation_on_preferred_entity_metrics",
    "score_typed_metrics",
    "select_preferred_pairs",
]
