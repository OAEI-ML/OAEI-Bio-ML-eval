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
from .loaders import load_global_submission, load_global_reference, load_local_gold, load_local_ranking
from .metrics import DEFAULT_HITS_KS, global_prf1, global_prf1_coherence_aware, local_ranking_metrics


def score_global_files(
    submission_path: str | Path,
    reference_path: str | Path,
    *,
    output_path: str | Path | None = None,
    deprecated: object = None,
) -> dict[str, float]:
    """
    Subtask 1: BOTH global P/R/F1 families against the reference — the standard set
    P/R/F1 over the complete reference, AND the LargeBio `?`-flagged `*_coherent` family
    that ignores the reference's incoherence-causing (`?`) mappings. The `?` subset is
    auto-detected inline in an RDF reference; a TSV reference carries no `?` so the two
    families coincide. Distinct keys — the families never overwrite each other.

    Scoring is relation-agnostic (existence only): the submission is read with
    load_global_submission (any asserted relation counts) and an Option-Two reference's
    subsumption cells are positives, so a predicted `=` matching a reference `<`/`>` (or the
    reverse) is credited — only the correspondence's existence is scored, never its relation.

    `deprecated` (optional): an iterable of owl:deprecated / obsolete IRIs, OUT-OF-TASK. When
    given, every correspondence touching one is dropped from the prediction AND the reference
    (and its `?` set) before scoring — the same out-of-task treatment the organiser applies (only
    the lite matchers predict any, so this changes their precision). Default None = no filtering.
    """
    predicted = load_global_submission(submission_path)
    reference, flagged = load_global_reference(reference_path)
    if deprecated:
        dep = set(deprecated)
        drop = lambda pairs: {(s, t) for (s, t) in pairs if s not in dep and t not in dep}
        predicted, reference, flagged = drop(predicted), drop(reference), drop(flagged)
    metrics = {
        **global_prf1(predicted, reference),
        **global_prf1_coherence_aware(predicted, reference, flagged),
    }
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
