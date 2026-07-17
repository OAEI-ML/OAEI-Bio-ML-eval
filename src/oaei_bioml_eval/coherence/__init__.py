"""
oaei_bioml_eval.coherence: organiser-computed Track 1 alignment coherence.

A degree of INCOHERENCE in [0, 1] — **0 = clean, higher = worse** (NOT a goodness
score). Two surfaces, named distinctly so the proxy is never read as the official
value:

  * OFFICIAL (reasoner-based, organiser-computed): merge the alignment + both OWLs
    (one EquivalentClasses per `=` correspondence), classify, report the
    unsatisfiable-class count and `global_coherence` = count / |merged signature|.
    `local_coherence` is the same over each query's rank-1 committed mapping. HermiT
    (DL, exact); ELK (EL) on the wall-clock gate, rendered `>=`. Behind a reasoner
    backend (ROBOT default subprocess; DeepOnto optional warm-JVM fast-path).
  * PROXY (dependency-free, participant self-guidance): `structural_coherence_proxy`
    — a reasoner-free structural heuristic (STUB until its rules are fixed).

The metric core (`metrics`) + the proxy (`structural`) are dependency-free; the
reasoner path needs the `[reasoner]` extra (rdflib + an external ROBOT) or the
`[deeponto]` extra. May import `hierarchy.HierarchyIndex` read-only; does not touch
the frozen typed/ API.
"""
from __future__ import annotations

from .metrics import (
    aggregate_across_tasks,
    _COUNT_METRICS_COHERENCE,
    global_coherence_ratio,
    local_coherence_aggregate,
    macro_average_across_tasks,
    micro_average_across_tasks,
)
from .reasoner import CoherenceReasoner, MergedOntology, UnsatResult, load_reasoner
from .report import (
    score_global_coherence_files,
    score_local_coherence_files,
    score_structural_proxy_files,
)

__all__ = [
    "aggregate_across_tasks",
    "CoherenceReasoner",
    "MergedOntology",
    "UnsatResult",
    "_COUNT_METRICS_COHERENCE",
    "global_coherence_ratio",
    "load_reasoner",
    "local_coherence_aggregate",
    "macro_average_across_tasks",
    "micro_average_across_tasks",
    "score_global_coherence_files",
    "score_local_coherence_files",
    "score_structural_proxy_files",
]
