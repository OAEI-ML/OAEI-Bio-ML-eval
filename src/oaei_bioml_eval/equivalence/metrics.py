"""
oaei_bioml_eval.equivalence.metrics: the Track 1 metric core.

Subtask 1 (global) -> set Precision/Recall/F1 over the COMPLETE many:many reference,
no null-reference exclusion (a rediscovered ranking-gold pair still counts — the
organiser's blind-global decision). 

Subtask 2 (local) -> MRR + Hits@{1,5,10} over one ranking query per reference mapping, 
one gold per list.

Pure + space-agnostic: the caller passes `(src, tgt)` pair sets and per-query ranked
target lists already in a single id space (the organiser maps IRIs/CURIEs before
scoring). Lifted from BioKG-Align's set + ranking helpers; the score quantize +
lexicographic tie-break match the typed half and candi-pool.
"""
from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from statistics import mean as _mean
from typing import TypeVar

from ..aggregation import AverageMode, validate_average, weighted_mean

_QUANTIZE = 12  # dp; BLAS-noise-stable sort, parity with typed/
DEFAULT_HITS_KS: tuple[int, ...] = (1, 5, 10)
QueryKey = TypeVar("QueryKey", bound=Hashable)

# score-based ranking may carry small floating-point noise (a few ulp) from upstream computation 
# since threaded and cross-vendor BLAS reductions are not neccesarily bit-reproducible (see ref). 
# we round to _QUANTIZE dp before the tie-broken sort, so near-equal scores collapse to exact ties 
# and are then ordered deterministically, related discussion:
# https://scicomp.stackexchange.com/questions/26137/are-blas-implementations-guaranteed-to-give-the-exact-same-result

# counts are summed (not averaged) across tasks; everything else is a rate. the
# `*_coherent`/`reference_*` counts are the `?`-flagged family's (global_prf1_coherence_aware).
_COUNT_METRICS = frozenset({
    "predicted", "reference", "true_positive", "queries",
    "true_positive_coherent", "reference_positive", "reference_flagged", "predicted_flagged",
})


def _safe_mean(values: list[float]) -> float:
    return _mean(values) if values else 0.0


##
# GLOBAL — set P/R/F1
# -------------------
##

def global_prf1(predicted: set[tuple[str, str]], reference: set[tuple[str, str]]) -> dict[str, float]:
    """set Precision/Recall/F1 of a global alignment vs the complete reference (m:n)"""
    true_positive = len(predicted & reference)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(reference) if reference else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "predicted": float(len(predicted)),
        "reference": float(len(reference)),
        "true_positive": float(true_positive),
    }


def global_prf1_coherence_aware(
    predicted: set[tuple[str, str]],
    reference: set[tuple[str, str]],
    flagged: set[tuple[str, str]],
) -> dict[str, float]:
    """
    LargeBio `?`-flagged P/R/F1: incoherence-causing reference mappings (`flagged`, the
    OAEI `?`/unknown relation) are IGNORED — neither a true nor a false positive, removed
    from BOTH denominators. With R the complete reference, U = flagged, R+ = R - U:

        precision = |A ∩ R+| / |A - U|   # a predicted `?` leaves the denominator
        recall    = |A ∩ R+| / |R+|      # the `?` mappings are not in the recall denominator

    Distinct `_coherent` keys — never overload the standard precision/recall/f1. The
    standard global_prf1 over the complete reference (where `?` cells still count as
    positives) is the companion metric; report both. Inspired by OAEI LargeBio (the UMLS
    reference regime). Pure + space-agnostic like global_prf1.
    """
    positive = reference - flagged           # R+ = R - U
    scored = predicted - flagged             # A - U  (a predicted `?` is neither TP nor FP)
    true_positive = len(scored & positive)   # == |A ∩ R+| (|(A-U) ∩ R+| = |A ∩ R+|)
    precision = true_positive / len(scored) if scored else 0.0
    recall = true_positive / len(positive) if positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision_coherent": precision,
        "recall_coherent": recall,
        "f1_coherent": f1,
        "true_positive_coherent": float(true_positive),
        "reference_positive": float(len(positive)),         # |R+|
        "reference_flagged": float(len(flagged)),           # |U| — the reference's incoherence load
        "predicted_flagged": float(len(predicted & flagged)),
    }


##
# LOCAL — MRR + Hits@k
# --------------------
##

def rank_by_score(candidates: Sequence[tuple[str, float]]) -> list[str]:
    """sort (target, score) best-first: quantized score desc, then target lexicographic"""
    return [tgt for tgt, _ in sorted(candidates, key=lambda c: (-round(c[1], _QUANTIZE), c[0]))]


def _rank_of(gold: str, ranked: Sequence[str]) -> int:
    """1-based rank of the gold in `ranked`; len+1 (a miss -> reciprocal 0) if absent"""
    try:
        return list(ranked).index(gold) + 1
    except ValueError:
        return len(ranked) + 1


def local_ranking_metrics(
    rankings: Mapping[QueryKey, Sequence[str]],
    golds: Mapping[QueryKey, str],
    *,
    hits_ks: tuple[int, ...] = DEFAULT_HITS_KS,
) -> dict[str, float]:
    """MRR + Hits@k over per-query rankings — one gold per query (View B)"""
    reciprocal: list[float] = []
    hits: dict[int, list[float]] = {k: [] for k in hits_ks}
    for key, gold in golds.items():
        ranked = rankings.get(key, [])
        rank = _rank_of(gold, ranked)
        found = rank <= len(ranked)
        reciprocal.append(1.0 / rank if found else 0.0)
        for k in hits_ks:
            hits[k].append(1.0 if found and rank <= k else 0.0)
    metrics = {"mrr": _safe_mean(reciprocal), "queries": float(len(golds))}
    for k in hits_ks:
        metrics[f"hits_at_{k}"] = _safe_mean(hits[k])
    return metrics


##
# MACRO
# -----
##

def macro_average_across_tasks(per_task: dict[str, dict[str, float]]) -> dict[str, float]:
    """arithmetic-mean the rate metrics across tasks; sum the count metrics"""
    out: dict[str, float] = {}
    for key in sorted({k for metrics in per_task.values() for k in metrics}):
        values = [metrics[key] for metrics in per_task.values() if key in metrics]
        out[key] = float(sum(values)) if key in _COUNT_METRICS else _safe_mean(values)
    return out


def micro_average_across_tasks(per_task: dict[str, dict[str, float]]) -> dict[str, float]:
    """Pool task sufficient statistics and recompute Conference-style metrics.

    Global precision/recall/F1 are recomputed from summed correspondence counts.
    Local MRR/Hits rates are weighted by their query counts. This is deliberately
    different from a macro mean when tasks have different sizes.
    """
    out: dict[str, float] = {
        key: float(
            sum(metrics.get(key, 0.0) for metrics in per_task.values())
        )
        for key in sorted(_COUNT_METRICS)
        if any(key in metrics for metrics in per_task.values())
    }

    handled_rates: set[str] = set()
    if any("true_positive" in metrics for metrics in per_task.values()):
        true_positive = out.get("true_positive", 0.0)
        predicted = out.get("predicted", 0.0)
        reference = out.get("reference", 0.0)
        precision = true_positive / predicted if predicted else 0.0
        recall = true_positive / reference if reference else 0.0
        out.update(
            precision=precision,
            recall=recall,
            f1=(
                2.0 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            ),
        )
        handled_rates.update(("precision", "recall", "f1"))

    if any("true_positive_coherent" in metrics for metrics in per_task.values()):
        true_positive = out.get("true_positive_coherent", 0.0)
        scored = out.get("predicted", 0.0) - out.get("predicted_flagged", 0.0)
        positive = out.get("reference_positive", 0.0)
        precision = true_positive / scored if scored else 0.0
        recall = true_positive / positive if positive else 0.0
        out.update(
            precision_coherent=precision,
            recall_coherent=recall,
            f1_coherent=(
                2.0 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            ),
        )
        handled_rates.update(
            ("precision_coherent", "recall_coherent", "f1_coherent")
        )

    rate_keys = sorted(
        {
            key
            for metrics in per_task.values()
            for key in metrics
            if key not in _COUNT_METRICS and key not in handled_rates
        }
    )
    for key in rate_keys:
        weighted = [
            (metrics[key], metrics["queries"])
            for metrics in per_task.values()
            if key in metrics and "queries" in metrics
        ]
        present = sum(key in metrics for metrics in per_task.values())
        if len(weighted) != present:
            raise ValueError(
                f"cannot micro-average {key!r}: a contributing task has no "
                "query-count denominator"
            )
        out[key] = weighted_mean(weighted)
    return out


def aggregate_across_tasks(
    per_task: dict[str, dict[str, float]],
    *,
    average: AverageMode = "macro",
) -> dict[str, float]:
    """Aggregate using equal-task macro or pooled-observation micro semantics."""
    mode = validate_average(average)
    if mode == "micro":
        return micro_average_across_tasks(per_task)
    return macro_average_across_tasks(per_task)
