"""
oaei_bioml_eval.coherence.metrics: the alignment-coherence metric core.

The OFFICIAL coherence (adopted from OAEI LargeBio): merge the alignment with both 
input ontologies (one EquivalentClasses(src, tgt) per `=` correspondence over named 
classes), classify, and report two things — the COUNT of unsatisfiable classes and 
the DEGREE of incoherence (count / |merged class signature|).

The degree reads BACKWARDS from a goodness score: it is a degree of INCOHERENCE
in [0, 1] where **0 = clean and higher = worse**. Documented loudly here and at
every call site so it is never mistaken for a coherence "score". (Repair-capable
matchers like AML/LogMap reach ~0; un-repaired lexical/BERTMap global alignments
sit non-zero — the LargeBio finding.)

Pure + reasoner-agnostic: the orchestrator (report.py) drives the reasoner and
hands this module the counts; here we only turn counts into the ratio and roll
per-task dicts up. Mirrors equivalence/metrics.py — 12-dp quantize, counts summed
across tasks, rates arithmetic-meaned.
"""
from __future__ import annotations

from collections.abc import Iterable


_QUANTIZE = 12  # dp; parity with equivalence/ + typed/ (canonical, BLAS-noise-stable)

# counts SUM across tasks; everything else (the degrees) is a rate, MEAN-ed. named
# distinctly from equivalence._COUNT_METRICS so the two never collide if merged.
_COUNT_METRICS_COHERENCE = frozenset({
    "unsatisfiable_count",
    "union_class_count",
    "local_coherence_queries",
    "structural_coherence_proxy_count",
})

# per-task annotations macro can't average (carried per cell, not aggregated)
_ANNOTATION_KEYS = frozenset({"reasoner_used", "lower_bound"})


def global_coherence_ratio(unsatisfiable_count: int, union_class_count: int) -> float:
    """
    degree of INCOHERENCE in [0, 1]: unsatisfiable_count / union_class_count, 0 =
    clean and higher = worse. an empty signature (nothing that could be incoherent)
    -> 0.0, not a divide-by-zero.
    """
    if union_class_count <= 0:
        return 0.0
    return round(unsatisfiable_count / union_class_count, _QUANTIZE)


def local_coherence_aggregate(per_query_incoherent: Iterable[bool]) -> dict[str, float]:
    """
    mean per-query incoherence over the queries that committed a rank-1 mapping.
    each flag is True iff that query's committed (src, top1) left src or top1
    unsatisfiable in the batched classification. a query that committed nothing is
    not counted (you cannot blame a mapping that was not made).
    """
    flags = [1.0 if bad else 0.0 for bad in per_query_incoherent]
    queries = len(flags)
    mean = round(sum(flags) / queries, _QUANTIZE) if queries else 0.0
    return {"local_coherence": mean, "local_coherence_queries": queries}


def macro_average_across_tasks(per_task: dict[str, dict[str, float]]) -> dict[str, float]:
    """
    roll per-task coherence dicts into one: SUM the counts, MEAN the rates, drop the
    per-task annotations (reasoner_used/lower_bound do not macro-average). mirrors
    equivalence.macro_average_across_tasks.
    """
    out: dict[str, float] = {}
    for key in sorted({k for metrics in per_task.values() for k in metrics}):
        if key in _ANNOTATION_KEYS:
            continue
        values = [m[key] for m in per_task.values() if key in m]
        numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not numeric:
            continue
        out[key] = float(sum(numeric)) if key in _COUNT_METRICS_COHERENCE else sum(numeric) / len(numeric)
    return out
