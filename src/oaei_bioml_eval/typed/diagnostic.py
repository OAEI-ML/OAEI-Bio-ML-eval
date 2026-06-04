"""
oaei_bioml_eval.typed.diagnostic: the two Track 2 diagnostic families.

Both are read-outs on the preferred entity, splitting the typed task into its two
halves. `score_entity_only_metrics` collapses the three relations to a per-entity
max and asks "did the system find the right *entity*, never mind the relation?" —
an untyped MRR/Hits ceiling. `score_relation_on_preferred_entity_metrics` asks the
complement: *given* the preferred entity, did it pick the right *relation*? —
accuracy plus a per-relation F1. Together they localise where a typed miss came
from (wrong entity vs wrong relation). Diagnostics, not headline numbers.
"""
from __future__ import annotations

from collections import defaultdict

from .metrics import (
    _filter_predictions_to_query_candidates,
    _quantize,
    _relation_sort_key,
    mean,
)


def score_entity_only_metrics(
    predictions: list[dict[str, str]],
    preferred_pairs: dict[tuple[str, str], list[tuple[str, str]]],
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, float]:
    """
    Entity-only (untyped) MRR + Hits@{1,5,10}. Per query: collapse each
    candidate's three relation scores to their max, rank entities by that
    (lexicographic id tie-break), and read off the preferred target's rank.
    """
    # (SrcEntity, QueryID) keying — see the note in metrics; grouping by source
    # alone would cross-contaminate a source's Q0 and Q1 rankings.
    by_query: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        by_query[(row["SrcEntity"], row.get("QueryID", "Q0"))].append(row)

    rrs: list[float] = []
    hits_at_1: list[float] = []
    hits_at_5: list[float] = []
    hits_at_10: list[float] = []

    for (source, query_id), pair_list in preferred_pairs.items():
        if not pair_list:
            continue
        cand_set = (
            per_query_candidate_sets.get((source, query_id))
            if per_query_candidate_sets is not None else None
        )
        query_rows = _filter_predictions_to_query_candidates(
            by_query.get((source, query_id), []), cand_set
        )

        per_entity_score: dict[str, float] = defaultdict(lambda: float("-inf"))
        for row in query_rows:
            score = _quantize(float(row.get("Score", 0.0)))
            tgt = row["TgtEntity"]
            if score > per_entity_score[tgt]:
                per_entity_score[tgt] = score

        ranked_entities = sorted(
            per_entity_score.items(),
            key=lambda item: (-item[1], item[0]),  # score desc, then entity id
        )
        entity_rank = {
            entity: idx
            for idx, (entity, _score) in enumerate(ranked_entities, start=1)
        }

        rank = entity_rank.get(pair_list[0][0])   # the preferred entity
        if rank is None:
            rrs.append(0.0)
            hits_at_1.append(0.0)
            hits_at_5.append(0.0)
            hits_at_10.append(0.0)
        else:
            rrs.append(1.0 / rank)
            hits_at_1.append(1.0 if rank <= 1 else 0.0)
            hits_at_5.append(1.0 if rank <= 5 else 0.0)
            hits_at_10.append(1.0 if rank <= 10 else 0.0)

    contributing = len(rrs)
    return {
        "entity_only_mrr": mean(rrs) if contributing else 0.0,
        "entity_only_hits_at_1": mean(hits_at_1) if contributing else 0.0,
        "entity_only_hits_at_5": mean(hits_at_5) if contributing else 0.0,
        "entity_only_hits_at_10": mean(hits_at_10) if contributing else 0.0,
    }


def score_relation_on_preferred_entity_metrics(
    predictions: list[dict[str, str]],
    preferred_pairs: dict[tuple[str, str], list[tuple[str, str]]],
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, float]:
    """
    Relation accuracy + per-relation F1, conditioned on the preferred entity. Per
    query: among the rows on the preferred target, take the highest-scored
    relation (tie-break equivalent < ssbt < sst) and compare it to the preferred
    relation. A query with no rows on the preferred target is undefined and
    skipped (well-formed cartesian-product submissions never hit that).
    """
    # (SrcEntity, QueryID) keying — see the note in metrics; grouping by source
    # alone would cross-contaminate a source's Q0 and Q1 rankings.
    by_query: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        by_query[(row["SrcEntity"], row.get("QueryID", "Q0"))].append(row)

    accuracy_observations: list[float] = []
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    for (source, query_id), pair_list in preferred_pairs.items():
        if not pair_list:
            continue
        cand_set = (
            per_query_candidate_sets.get((source, query_id))
            if per_query_candidate_sets is not None else None
        )
        source_rows = _filter_predictions_to_query_candidates(
            by_query.get((source, query_id), []), cand_set
        )
        for preferred_target, preferred_relation in pair_list:
            scores_on_v: dict[str, float] = {}
            for row in source_rows:
                if row["TgtEntity"] != preferred_target:
                    continue
                rel = row["Relation"]
                score = _quantize(float(row.get("Score", 0.0)))
                if rel not in scores_on_v or score > scores_on_v[rel]:
                    scores_on_v[rel] = score
            if not scores_on_v:
                continue                          # preferred entity absent

            predicted_relation = min(
                scores_on_v.keys(),
                key=lambda r: (-scores_on_v[r], _relation_sort_key(r)),
            )
            accuracy_observations.append(
                1.0 if predicted_relation == preferred_relation else 0.0
            )
            if predicted_relation == preferred_relation:
                tp[preferred_relation] += 1
            else:
                fp[predicted_relation] += 1
                fn[preferred_relation] += 1

    contributing = len(accuracy_observations)
    accuracy = mean(accuracy_observations) if contributing else 0.0

    per_relation_f1: dict[str, float] = {}
    for relation in sorted(set(tp) | set(fp) | set(fn)):
        p_denom = tp[relation] + fp[relation]
        r_denom = tp[relation] + fn[relation]
        precision = tp[relation] / p_denom if p_denom else 0.0
        recall = tp[relation] / r_denom if r_denom else 0.0
        per_relation_f1[relation] = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) else 0.0
        )
    macro_f1 = mean(list(per_relation_f1.values())) if per_relation_f1 else 0.0

    result: dict[str, float] = {
        "relation_accuracy_on_preferred_entity": accuracy,
        "relation_macro_f1_on_preferred_entity": macro_f1,
    }
    for relation, f1 in per_relation_f1.items():
        result[f"relation_f1_on_preferred_entity_{relation}"] = f1
    return result
