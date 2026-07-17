"""
oaei_bioml_eval.coherence: organiser-computed Track 1 alignment coherence.

A degree of INCOHERENCE in [0, 1] — **0 = clean, higher = worse** (NOT a goodness
score). Two surfaces, named distinctly so the proxy is never read as the official
value:

  * OFFICIAL (reasoner-based, organiser-computed): compose the alignment bridge over
    both shared OWL views, classify once, and report the unsatisfiable-class count
    plus `global_coherence` = count / |composite signature|. `local_coherence` is
    the same over each query's rank-1 commitment. Native pyHermiT provides the exact
    DL result; a cooperative HermiT timeout alone falls back to pyELK over the same
    composite and is labeled as an EL lower bound.
  * PROXY (dependency-free, participant self-guidance): `structural_coherence_proxy`
    — a reasoner-free structural heuristic (STUB until its rules are fixed).

The metric core (`metrics`) + proxy (`structural`) remain dependency-free. Native
reasoner packages are imported lazily only by official classification. This module
does not touch the frozen typed/ API.
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
from .native_reasoners import (
    ELKReasoner,
    ELKTimeoutError,
    HermiTReasoner,
    HermiTTimeoutError,
    NativeReasoner,
    NativeReasonerCompatibilityError,
    NativeReasonerUnavailableError,
    NativeWorkerError,
)
from .reasoner import CoherenceReasoner, MergedOntology, UnsatResult, load_reasoner
from .report import (
    SnapshotCompatibilityError,
    score_global_coherence,
    score_global_coherence_files,
    score_local_coherence,
    score_local_coherence_files,
    score_reference_coherence,
    score_reference_coherence_files,
    score_structural_proxy_files,
)

__all__ = [
    "aggregate_across_tasks",
    "CoherenceReasoner",
    "ELKReasoner",
    "ELKTimeoutError",
    "HermiTReasoner",
    "HermiTTimeoutError",
    "MergedOntology",
    "UnsatResult",
    "_COUNT_METRICS_COHERENCE",
    "global_coherence_ratio",
    "load_reasoner",
    "local_coherence_aggregate",
    "macro_average_across_tasks",
    "micro_average_across_tasks",
    "NativeReasoner",
    "NativeReasonerCompatibilityError",
    "NativeReasonerUnavailableError",
    "NativeWorkerError",
    "SnapshotCompatibilityError",
    "score_global_coherence",
    "score_global_coherence_files",
    "score_local_coherence",
    "score_local_coherence_files",
    "score_reference_coherence",
    "score_reference_coherence_files",
    "score_structural_proxy_files",
]
