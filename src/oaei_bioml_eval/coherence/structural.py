"""
oaei_bioml_eval.coherence.structural: the dependency-free structural coherence PROXY.

A reasoner-free participant self-guidance signal — flag (src, tgt)
correspondences that violate the target hierarchy's structure, score
`flagged_pairs / total_pairs`, key it `structural_coherence_proxy`. It is NEVER the
leaderboard value (that is the reasoner-based `global_coherence`); the distinct key
keeps the two from being confused.

STUB. Candidate rules (TBD):
  * disjoint-ancestor violation — two correspondences map one source under
    target classes with disjoint ancestors;
  * sibling / many-to-one clash — many sources collapse onto one target sibling-set;
  * cross-hierarchy subsumption inversion — a correspondence inverts an asserted
    SubClassOf direction across the two ontologies.
May read the frozen `hierarchy.HierarchyIndex` READ-ONLY.
"""
from __future__ import annotations

from typing import Any


def structural_coherence_proxy(*args: Any, **kwargs: Any) -> dict[str, float]:
    """STUB — the structural-proxy rule set is open"""
    raise NotImplementedError(
        "the structural-proxy rule set is not yet fixed (open); "
        "the official reasoner-based global_coherence is the leaderboard value."
    )
