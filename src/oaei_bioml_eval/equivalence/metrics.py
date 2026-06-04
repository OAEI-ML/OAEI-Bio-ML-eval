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

from collections.abc import Hashable, Sequence
from statistics import mean as _mean


_QUANTIZE = 12  # dp; BLAS-noise-stable sort, parity with typed/
DEFAULT_HITS_KS: tuple[int, ...] = (1, 5, 10)

# score-based ranking may carry small floating-point noise (a few ulp) from upstream computation 
# since threaded and cross-vendor BLAS reductions are not neccesarily bit-reproducible (see ref). 
# we round to _QUANTIZE dp before the tie-broken sort, so near-equal scores collapse to exact ties 
# and are then ordered deterministically, related discussion:
# https://scicomp.stackexchange.com/questions/26137/are-blas-implementations-guaranteed-to-give-the-exact-same-result

# counts are summed (not averaged) across tasks; everything else is a rate
_COUNT_METRICS = frozenset({"predicted", "reference", "true_positive", "queries"})


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
    rankings: dict[Hashable, Sequence[str]],
    golds: dict[Hashable, str],
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
