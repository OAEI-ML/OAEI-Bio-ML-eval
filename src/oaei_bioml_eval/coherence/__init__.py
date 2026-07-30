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
  * RESERVED PROXY API: `structural_coherence_proxy` — unavailable while its
    rule set remains open; the current stub raises `NotImplementedError`.

The metric core (`metrics`) remains dependency-free. Native
reasoner packages are imported lazily only by official classification. This module
does not touch the frozen typed/ API.
"""
from __future__ import annotations

from .bridge import SnapshotCompatibilityError
from .metrics import (
    _COUNT_METRICS_COHERENCE,
    aggregate_across_tasks,
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
from .reasoner import CoherenceReasoner, UnsatResult, load_reasoner
from .report import (
    score_global_coherence,
    score_global_coherence_files,
    score_local_coherence,
    score_local_coherence_files,
    score_reference_coherence,
    score_reference_coherence_files,
    score_structural_proxy_files,
)

__all__ = [
    "_COUNT_METRICS_COHERENCE",
    "CoherenceReasoner",
    "ELKReasoner",
    "ELKTimeoutError",
    "HermiTReasoner",
    "HermiTTimeoutError",
    "NativeReasoner",
    "NativeReasonerCompatibilityError",
    "NativeReasonerUnavailableError",
    "NativeWorkerError",
    "SnapshotCompatibilityError",
    "UnsatResult",
    "aggregate_across_tasks",
    "global_coherence_ratio",
    "load_reasoner",
    "local_coherence_aggregate",
    "macro_average_across_tasks",
    "micro_average_across_tasks",
    "score_global_coherence",
    "score_global_coherence_files",
    "score_local_coherence",
    "score_local_coherence_files",
    "score_reference_coherence",
    "score_reference_coherence_files",
    "score_structural_proxy_files",
]
