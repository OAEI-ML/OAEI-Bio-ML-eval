"""
oaei_bioml_eval.typed.report: the file-level entry point + the cross-task macro.

`score_files` is the one-call scorer — point it at a block submission and an
answers file, optionally a preferred/graded/hierarchy file, and it returns (and
optionally writes) the metric dict. `macro_average_across_tasks` rolls the
per-(task, baseline) dicts up into one row per baseline, arithmetic-averaging the
rate metrics across tasks and summing/averaging the counts — the leaderboard's
cross-task view.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..hierarchy import HierarchyIndex
from ..io import read_tsv, write_json
from .loaders import (
    load_answers,
    load_block_format_predictions,
    load_graded_relevance,
    load_per_query_candidate_sets_from_answers,
    load_preferred_pairs,
)
from .metrics import DEFAULT_RELATIONS, score_typed_metrics
from .relevance import DEFAULT_MAX_DISTANCE, select_preferred_pairs

# count metrics get a _sum and a _mean across tasks (and the bare name kept as a
# _sum alias); everything else is a rate, arithmetic-meaned.
_COUNT_METRICS = frozenset({
    "queries",
    "preferred_pair_queries",
    "preferred_pair_queries__equivalence_only",
    "preferred_pair_queries__subsumption_only",
    "hierarchy_aware_typed_ndcg_at_10_queries",
    "hierarchy_aware_typed_ndcg_at_10__equivalence_only_queries",
    "hierarchy_aware_typed_ndcg_at_10__subsumption_only_queries",
})


def _group_by_baseline(
    results: dict[str, dict[str, float]], separator: str
) -> dict[str, list[dict[str, float]]]:
    """Group `{task<sep>baseline: metrics}` entries by baseline."""
    grouped: dict[str, list[dict[str, float]]] = defaultdict(list)
    for key, metrics in results.items():
        if separator not in key:
            continue
        _task, baseline = key.split(separator, 1)
        if not baseline:
            continue
        grouped[baseline].append(metrics)
    return grouped


def macro_average_across_tasks(
    per_task_results: dict[str, dict[str, float]], separator: str = "/"
) -> dict[str, dict[str, float]]:
    """
    Macro-average each baseline's metrics across the tasks it ran on. Input keys
    are `f"{task}{separator}{baseline}"`; the output is one row per baseline.

    Rate metrics are arithmetic-meaned across tasks. Count metrics (see
    `_COUNT_METRICS`) get both a `_sum` and a `_mean`, with the unsuffixed name
    preserved as an alias of `_sum`. Each row also carries a `tasks` count.
    """
    grouped = _group_by_baseline(per_task_results, separator)

    aggregated: dict[str, dict[str, float]] = {}
    for baseline_name, task_metrics_list in sorted(grouped.items()):
        all_keys: set[str] = set()
        for task_metrics in task_metrics_list:
            all_keys.update(task_metrics.keys())

        row: dict[str, float] = {}
        for key in sorted(all_keys):
            values = [m[key] for m in task_metrics_list if key in m]
            if not values:
                continue
            if key in _COUNT_METRICS:
                total = float(sum(values))
                row[f"{key}_sum"] = total
                row[f"{key}_mean"] = total / len(values)
                row[key] = total                 # back-compat: bare name == _sum
            else:
                row[key] = sum(values) / len(values)
        row["tasks"] = float(len(task_metrics_list))
        aggregated[baseline_name] = row

    return aggregated


def score_files(
    predictions_path: str | Path,
    answers_path: str | Path,
    *,
    output_path: str | Path | None = None,
    preferred_pairs_path: str | Path | None = None,
    graded_relevance_path: str | Path | None = None,
    hierarchy_path: str | Path | None = None,
    submission_format: str = "block",
    relations: list[str] | tuple[str, ...] = DEFAULT_RELATIONS,
    candidate_count: int = 50,
    k: int = 10,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> dict[str, float]:
    """
    Score a Track 2 submission against an answers file; optionally write the
    metric dict as JSON. Returns the dict either way.

    The candidate pools come from the answers file. Preferred pairs are read from
    `preferred_pairs_path` if given, else derived from the answers via the frozen
    `select_preferred_pairs`. For H-nDCG, pass a `graded_relevance_path` (consume
    the organiser's gains) OR a `hierarchy_path` (recompute them in-process via
    `relevance.compute_graded_relevance`); with neither, H-nDCG is omitted. A
    `graded_relevance_path` takes precedence over `hierarchy_path`.
    """
    if submission_format == "block":
        predictions = load_block_format_predictions(
            predictions_path, answers_path,
            relations=tuple(relations), candidate_count=int(candidate_count),
        )
    elif submission_format == "row":
        predictions = read_tsv(predictions_path)
    else:
        raise ValueError(
            f"score_files: submission_format must be 'block' or 'row', got "
            f"{submission_format!r}."
        )

    answers = load_answers(answers_path)
    per_query_candidate_sets = load_per_query_candidate_sets_from_answers(
        answers_path
    )

    loaded_preferred = (
        load_preferred_pairs(preferred_pairs_path)
        if preferred_pairs_path else {}
    )
    preferred_pairs = loaded_preferred or {
        key: select_preferred_pairs(pairs) for key, pairs in answers.items()
    }

    graded_relevance = (
        load_graded_relevance(graded_relevance_path)
        if graded_relevance_path else None
    )
    hierarchy = (
        HierarchyIndex(read_tsv(hierarchy_path))
        if hierarchy_path and graded_relevance is None else None
    )

    metrics = score_typed_metrics(
        predictions, answers, preferred_pairs,
        graded_relevance=graded_relevance,
        hierarchy=hierarchy,
        per_query_candidate_sets=per_query_candidate_sets,
        k=k, max_distance=max_distance,
    )
    if output_path:
        write_json(output_path, metrics)
    return metrics
