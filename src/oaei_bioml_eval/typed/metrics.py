"""
oaei_bioml_eval.typed.metrics: the Track 2 typed ranking metrics.

The two headline numbers are the **Preferred Relation-Aware (Typed) MRR** and the
**Hierarchy-Aware Typed nDCG@10**, both anchored on the one preferred (target,
relation) gold the frozen `relevance.select_preferred_pairs` picks per query.
Preferred MRR asks "how high did the participant rank that exact pair?"; H-nDCG@10
softens it with the graded gains from `relevance.compute_graded_relevance` (exact
1.0, same-entity subsumption 0.6, hierarchical near-misses decaying as 1/(d+1)).

We deliberately keep the surface narrow — the preferred-pair family + H-nDCG (with
its Q0/Q1 slices) + the diagnostics in `diagnostic.py`. The untyped binary
ranking metrics, the macro-across-relations `typed_*` family, and the per-relation
P/R/F1 that BioKG-Align's scorer also emitted are dropped (we keep the public surface 
to the documented Track 2 families).

Two determinism choices follow the eval brief and so deviate from BioKG-Align:
scores are **quantized** before the sort, and the ranking tie-break is **relation
order then lexicographic target** (BioKG broke ties target-first).
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from ..hierarchy import HierarchyIndex
from .relevance import (
    DEFAULT_MAX_DISTANCE,
    _PREFERRED_RELATION_ORDER,
    compute_graded_relevance,
)

# the relation vocabulary, in preference order — single-sourced from the frozen
# relevance module so the scorer and the gold build can never disagree on it.
DEFAULT_RELATIONS: tuple[str, ...] = tuple(_PREFERRED_RELATION_ORDER)

# round scores to this many dp before sorting. collapses sub-threshold float
# noise (BLAS / accumulation) into honest ties; real score gaps sit far above
# 1e-12. matches candi-pool's lexical-score quantization for cross-package
# consistency. raise/lower only if a determinism issue shows up in practice.
_SCORE_DP: int = 12


def _quantize(score: float) -> float:
    return round(score, _SCORE_DP)


def _relation_sort_key(relation: str) -> int:
    """Tie-break rank for a relation; anything unknown sorts last."""
    return _PREFERRED_RELATION_ORDER.get(relation, len(_PREFERRED_RELATION_ORDER))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


##
# RANKING PRIMITIVES
# ------------------
# turn a query's prediction rows into a single deterministic ranked list, then
# answer "where did this (target, relation) pair land?" against it.
##


def _rank_predictions_for_source(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Sort one query's rows into a single ranking and drop duplicate (target,
    relation) pairs, keeping the highest-scored. Order: quantized score desc,
    then relation (equivalent < ssbt < sst), then lexicographic target.
    """
    ranked = sorted(
        rows,
        key=lambda row: (
            -_quantize(float(row.get("Score", 0.0))),
            _relation_sort_key(row["Relation"]),
            row["TgtEntity"],
        ),
    )
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for row in ranked:
        key = (row["TgtEntity"], row["Relation"])
        if key in seen:                          # first occurrence is the max
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _filter_predictions_to_query_candidates(
    source_predictions: list[dict[str, str]],
    candidate_set: set[str] | None,
) -> list[dict[str, str]]:
    """Restrict a source's rows to one query's candidate pool; None -> no-op."""
    if candidate_set is None:
        return source_predictions
    return [
        row for row in source_predictions
        if row.get("TgtEntity") in candidate_set
    ]


def _rank_of_pair_in_ranked(
    ranked: list[dict[str, str]], target: str, relation: str
) -> int | None:
    """1-indexed position of the first (target, relation) match, else None."""
    for idx, row in enumerate(ranked, start=1):
        if row["TgtEntity"] == target and row["Relation"] == relation:
            return idx
    return None


def hierarchy_aware_ndcg(
    ranked_predictions: list[dict[str, str]],
    graded_relevance: dict[tuple[str, str], float],
    k: int = 10,
) -> float:
    """
    Hierarchy-Aware Typed nDCG@K for one query against continuous gains:

        DCG@K  = sum_{i=1..K} gain(p_i) / log2(i + 1)
        IDCG@K = DCG@K of the K largest available gains
        nDCG@K = DCG@K / IDCG@K, defined as 0 when no positive gain exists.

    `ranked_predictions` is already sorted (see `_rank_predictions_for_source`);
    `graded_relevance` is the per-query `(target, relation) -> gain` lookup.
    """
    dcg = 0.0
    for i, row in enumerate(ranked_predictions[:k], start=1):
        gain = graded_relevance.get((row["TgtEntity"], row["Relation"]), 0.0)
        dcg += gain / math.log2(i + 1)

    sorted_gains = sorted(graded_relevance.values(), reverse=True)
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(sorted_gains[:k], 1))
    if idcg == 0.0:                              # no positive gains -> 0 by spec
        return 0.0
    return dcg / idcg


##
# PREFERRED-PAIR-ANCHORED METRICS  (the headline MRR + secondaries)
##


def score_preferred_typed_metrics(
    predictions: list[dict[str, str]],
    preferred_pairs: dict[tuple[str, str], list[tuple[str, str]]],
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, float]:
    """
    Rank of the preferred pair, per query: RR = 1/rank, and the Hits@{1,5,10} /
    median-rank companions. A query with several preferred pairs (a 1:many eq
    query) takes the best (lowest) rank across them. Queries absent from
    `preferred_pairs` drop out of both numerator and denominator.
    """
    # key on (SrcEntity, QueryID), NOT SrcEntity alone — one source poses both
    # Q0 and Q1 over overlapping candidate pools, so grouping by source would
    # leak a query's rows into its sibling's ranking (the contract's keying).
    by_query: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        by_query[(row["SrcEntity"], row.get("QueryID", "Q0"))].append(row)

    rrs: list[float] = []
    hits_at_1: list[float] = []
    hits_at_5: list[float] = []
    hits_at_10: list[float] = []
    median_ranks: list[float] = []

    for (source, query_id), pair_list in preferred_pairs.items():
        if not pair_list:
            continue
        cand_set = (
            per_query_candidate_sets.get((source, query_id))
            if per_query_candidate_sets is not None else None
        )
        ranked = _rank_predictions_for_source(
            _filter_predictions_to_query_candidates(
                by_query.get((source, query_id), []), cand_set
            )
        )
        no_hit_rank = float(len(ranked) + 1)     # one past the list end

        best_rank = no_hit_rank
        any_hit = False
        for preferred_target, preferred_relation in pair_list:
            r = _rank_of_pair_in_ranked(
                ranked, preferred_target, preferred_relation
            )
            if r is not None:
                any_hit = True
                best_rank = min(best_rank, float(r))

        if not any_hit:
            rrs.append(0.0)
            hits_at_1.append(0.0)
            hits_at_5.append(0.0)
            hits_at_10.append(0.0)
            median_ranks.append(no_hit_rank)
        else:
            rrs.append(1.0 / best_rank)
            hits_at_1.append(1.0 if best_rank <= 1 else 0.0)
            hits_at_5.append(1.0 if best_rank <= 5 else 0.0)
            hits_at_10.append(1.0 if best_rank <= 10 else 0.0)
            median_ranks.append(best_rank)

    contributing = len(rrs)
    return {
        "preferred_typed_mrr": mean(rrs) if contributing else 0.0,
        "preferred_typed_hits_at_1": mean(hits_at_1) if contributing else 0.0,
        "preferred_typed_hits_at_5": mean(hits_at_5) if contributing else 0.0,
        "preferred_typed_hits_at_10": mean(hits_at_10) if contributing else 0.0,
        "median_preferred_typed_rank": (
            float(statistics.median(median_ranks)) if contributing else 0.0
        ),
        "preferred_pair_queries": float(contributing),
    }


def _partition_preferred_by_relation(
    preferred_pairs: dict[tuple[str, str], list[tuple[str, str]]],
) -> tuple[dict, dict]:
    """
    Split the preferred pairs into the equivalence (Q0) and subsumption-only (Q1)
    query sets, by the preferred relation. Under the N=1 invariant this is the
    same partition the H-nDCG slices use (which classify by gold relations) — a
    test pins the two classifications together.
    """
    eq: dict[tuple[str, str], list[tuple[str, str]]] = {}
    sub: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, pairs in preferred_pairs.items():
        if not pairs:
            continue
        (eq if pairs[0][1] == "equivalent" else sub)[key] = pairs
    return eq, sub


##
# THE PER-TASK SCORER
# -------------------
# stitches H-nDCG (with its Q0/Q1 slices) onto the preferred-pair family and the
# diagnostics. graded relevance is either supplied (a graded.tsv the organiser
# emitted) or computed here from the preferred pairs + a HierarchyIndex via the
# frozen gain fn — both routes give the same H-nDCG, by construction.
##


def score_typed_metrics(
    predictions: list[dict[str, str]],
    answers: dict[tuple[str, str], set[tuple[str, str]]],
    preferred_pairs: dict[tuple[str, str], list[tuple[str, str]]],
    *,
    graded_relevance: dict[tuple[str, str], dict[tuple[str, str], float]]
    | None = None,
    hierarchy: HierarchyIndex | None = None,
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
    k: int = 10,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> dict[str, float]:
    """
    Score a Track 2 prediction list (one task) against per-query gold.

    `predictions` are enriched rows (`SrcEntity, QueryID, TgtEntity, Relation,
    Score`); `answers`/`preferred_pairs`/`per_query_candidate_sets` all key on
    `(SrcEntity, QueryID)`. For H-nDCG, pass either `graded_relevance` OR a
    `hierarchy` (the gains are then computed per query from the preferred pair and
    its candidate pool — which therefore requires `per_query_candidate_sets`).
    With neither, the H-nDCG family is omitted.
    """
    if (
        graded_relevance is None
        and hierarchy is not None
        and per_query_candidate_sets is None
    ):
        raise ValueError(
            "score_typed_metrics: computing graded relevance from a hierarchy "
            "needs per_query_candidate_sets (the gains are scoped to each "
            "query's candidate pool)."
        )
    graded_available = graded_relevance is not None or hierarchy is not None

    # key on (SrcEntity, QueryID), NOT SrcEntity alone — one source poses both
    # Q0 and Q1 over overlapping candidate pools, so grouping by source would
    # leak a query's rows into its sibling's ranking (the contract's keying).
    by_query: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        by_query[(row["SrcEntity"], row.get("QueryID", "Q0"))].append(row)

    haw_all: list[float] = []
    haw_eq: list[float] = []                     # Q0 — gold includes equivalence
    haw_sub: list[float] = []                    # Q1 — subsumption-only gold

    for (source, query_id), gold in sorted(answers.items()):
        cand_set = (
            per_query_candidate_sets.get((source, query_id))
            if per_query_candidate_sets is not None else None
        )
        ranked = _rank_predictions_for_source(
            _filter_predictions_to_query_candidates(
                by_query.get((source, query_id), []), cand_set
            )
        )

        graded_q: dict[tuple[str, str], float] | None = None
        if graded_relevance is not None:
            graded_q = graded_relevance.get((source, query_id))
        elif hierarchy is not None:
            pairs = preferred_pairs.get((source, query_id))
            if pairs and cand_set is not None:
                # single preferred pair per query (the N=1 Track 2 invariant)
                graded_q = compute_graded_relevance(
                    pairs[0], cand_set, hierarchy, max_distance
                )

        if graded_q is not None:
            haw = hierarchy_aware_ndcg(ranked, graded_q, k)
            haw_all.append(haw)
            is_sub_only = "equivalent" not in {rel for _tgt, rel in gold}
            (haw_sub if is_sub_only else haw_eq).append(haw)

    metrics: dict[str, float] = {"queries": float(len(answers))}

    if graded_available:
        metrics["hierarchy_aware_typed_ndcg_at_10"] = mean(haw_all)
        metrics["hierarchy_aware_typed_ndcg_at_10__equivalence_only"] = mean(
            haw_eq
        )
        metrics["hierarchy_aware_typed_ndcg_at_10__subsumption_only"] = mean(
            haw_sub
        )
        metrics["hierarchy_aware_typed_ndcg_at_10_queries"] = float(len(haw_all))
        metrics[
            "hierarchy_aware_typed_ndcg_at_10__equivalence_only_queries"
        ] = float(len(haw_eq))
        metrics[
            "hierarchy_aware_typed_ndcg_at_10__subsumption_only_queries"
        ] = float(len(haw_sub))

    # the preferred-pair family, plus its Q0/Q1 (eq/sub) slices — same scoring
    # code over the partitioned query sets, so the slices can't drift from the
    # blended numbers. diagnostics are deliberately left un-sliced.
    metrics.update(
        score_preferred_typed_metrics(
            predictions, preferred_pairs, per_query_candidate_sets
        )
    )
    eq_pairs, sub_pairs = _partition_preferred_by_relation(preferred_pairs)
    for suffix, subset in (("__equivalence_only", eq_pairs),
                           ("__subsumption_only", sub_pairs)):
        for key, value in score_preferred_typed_metrics(
            predictions, subset, per_query_candidate_sets
        ).items():
            metrics[f"{key}{suffix}"] = value

    # diagnostics imported here to keep the module-load graph acyclic
    from .diagnostic import (
        score_entity_only_metrics,
        score_relation_on_preferred_entity_metrics,
    )

    metrics.update(
        score_entity_only_metrics(
            predictions, preferred_pairs, per_query_candidate_sets
        )
    )
    metrics.update(
        score_relation_on_preferred_entity_metrics(
            predictions, preferred_pairs, per_query_candidate_sets
        )
    )
    return metrics
