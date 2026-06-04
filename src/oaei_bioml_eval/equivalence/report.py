"""
oaei_bioml_eval.equivalence.report: file -> metric-dict entry points.

`score_global_files` reads a global alignment submission + the complete reference
and returns set P/R/F1; `score_local_files` reads a local ranking submission + the
per-query gold and returns MRR/Hits@k. Both optionally write the metric dict as
JSON. Caller-supplied files must already share one id space (the organiser maps
IRIs/CURIEs before scoring).
"""
from __future__ import annotations

from pathlib import Path

from ..io import write_json
from .loaders import load_global_pairs, load_local_gold, load_local_ranking
from .metrics import DEFAULT_HITS_KS, global_prf1, local_ranking_metrics


def score_global_files(
    submission_path: str | Path,
    reference_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, float]:
    """Subtask 1: set P/R/F1 of a global alignment vs the complete reference"""
    metrics = global_prf1(load_global_pairs(submission_path), load_global_pairs(reference_path))
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_local_files(
    submission_path: str | Path,
    gold_path: str | Path,
    *,
    candidate_count: int = 50,
    hits_ks: tuple[int, ...] = DEFAULT_HITS_KS,
    output_path: str | Path | None = None,
) -> dict[str, float]:
    """Subtask 2: MRR + Hits@k of a local ranking submission vs the per-query gold"""
    rankings = load_local_ranking(submission_path, candidate_count)
    gold = load_local_gold(gold_path)
    if len(rankings) != len(gold):
        raise ValueError(
            f"local ranking has {len(rankings)} queries but the gold has {len(gold)}; "
            f"check the submission row order / candidate_count={candidate_count}."
        )
    rank_by_query: dict[int, list[str]] = {}
    gold_by_query: dict[int, str] = {}
    for index, ((sub_src, ranking), (gold_src, target)) in enumerate(zip(rankings, gold)):
        if sub_src != gold_src:
            raise ValueError(
                f"query {index}: submission source {sub_src!r} != gold source {gold_src!r}; "
                "the ranking rows must follow the gold's query order."
            )
        rank_by_query[index] = ranking
        gold_by_query[index] = target
    metrics = local_ranking_metrics(rank_by_query, gold_by_query, hits_ks=hits_ks)
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics
