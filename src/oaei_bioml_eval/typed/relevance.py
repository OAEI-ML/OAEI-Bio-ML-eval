"""
oaei_bioml_eval.typed.relevance: preferred-pair selection + the hierarchy-aware
graded-relevance gain function for Track 2 (Typed MRR + H-nDCG@10).

Exactly one preferred (target, relation) gold per query — equivalence is
preferred over either subsumption direction. The gain function scores an exact
match 1.0; the two same-entity subsumption directions 0.6 each (the equivalence
biconditional A≡B ⟺ A⊑B ∧ A⊒B — predicting one direction on the right entity is
half-right); and hierarchical near-misses a 1/(d+1)-decayed share by shortest-path
depth. Only non-zero gains are returned.
"""
from __future__ import annotations

from ..hierarchy import HierarchyIndex

# graded relevance looks out to this hop distance; gain decays as .../(d+1)
DEFAULT_MAX_DISTANCE: int = 3

# relation precedence: equivalence first, then the two subsumption directions
_PREFERRED_RELATION_ORDER: dict[str, int] = {
    "equivalent": 0,
    "source_subsumed_by_target": 1,
    "source_subsumes_target": 2,
}


def select_preferred_pairs(gold_pairs: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Pick the preferred (target, relation) gold from a query's gold set by the
    precedence policy — the first non-empty (sorted) of equivalent ->
    source_subsumed_by_target -> source_subsumes_target. Under the N=1 invariant
    this returns a singleton; a 1:many equivalence query returns the sorted list.
    """
    if not gold_pairs:
        raise ValueError("select_preferred_pairs: gold set is empty; each query needs >=1 gold pair.")

    equivalent_pairs = sorted((tgt, rel) for (tgt, rel) in gold_pairs if rel == "equivalent")
    if equivalent_pairs:
        return equivalent_pairs

    ssbt_pairs = sorted((tgt, rel) for (tgt, rel) in gold_pairs if rel == "source_subsumed_by_target")
    if ssbt_pairs:
        return ssbt_pairs

    sst_pairs = sorted((tgt, rel) for (tgt, rel) in gold_pairs if rel == "source_subsumes_target")
    if sst_pairs:
        return sst_pairs

    raise ValueError("select_preferred_pairs: gold set contains no recognised relation.")


def _single_preferred_gains(
    preferred_target: str,
    preferred_relation: str,
    candidate_set: set[str],
    hierarchy: HierarchyIndex,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> dict[tuple[str, str], float]:
    if preferred_relation not in _PREFERRED_RELATION_ORDER:
        raise ValueError(f"Unknown preferred_relation: {preferred_relation!r}")

    gains: dict[tuple[str, str], float] = {}

    # exact match — always 1.0 when the preferred target is a candidate
    if preferred_target in candidate_set:
        gains[(preferred_target, preferred_relation)] = 1.0

    if preferred_relation == "equivalent":
        # same entity, either subsumption direction: 0.6 each (the biconditional)
        if preferred_target in candidate_set:
            gains[(preferred_target, "source_subsumed_by_target")] = 0.6
            gains[(preferred_target, "source_subsumes_target")] = 0.6
        # ancestors of v*: broader candidates the source is also subsumed by
        for ancestor, dist in hierarchy.ancestors_with_distance(preferred_target, max_distance).items():
            if ancestor in candidate_set:
                gains[(ancestor, "source_subsumed_by_target")] = 0.6 / (dist + 1)
        # descendants of v*: narrower candidates the source also subsumes
        for descendant, dist in hierarchy.descendants_with_distance(preferred_target, max_distance).items():
            if descendant in candidate_set:
                gains[(descendant, "source_subsumes_target")] = 0.6 / (dist + 1)

    elif preferred_relation == "source_subsumed_by_target":
        # no same-entity partial credit (eq over-claims, sst is the wrong direction);
        # ancestors of v* at distance d score 1/(d+1)
        for ancestor, dist in hierarchy.ancestors_with_distance(preferred_target, max_distance).items():
            if ancestor in candidate_set:
                gains[(ancestor, "source_subsumed_by_target")] = 1.0 / (dist + 1)

    elif preferred_relation == "source_subsumes_target":
        # symmetric: descendants of v* at distance d score 1/(d+1)
        for descendant, dist in hierarchy.descendants_with_distance(preferred_target, max_distance).items():
            if descendant in candidate_set:
                gains[(descendant, "source_subsumes_target")] = 1.0 / (dist + 1)

    return gains


def compute_graded_relevance(
    preferred_pair: tuple[str, str],
    candidate_set: set[str],
    hierarchy: HierarchyIndex,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> dict[tuple[str, str], float]:
    """
    Graded-relevance gains for H-nDCG@10 over one query's preferred pair. Exact
    match 1.0; same-entity subsumption directions 0.6 (eq-preferred); hierarchical
    partial credit decaying as gain/(d+1). Only non-zero entries are returned.
    """
    preferred_target, preferred_relation = preferred_pair
    return _single_preferred_gains(
        preferred_target,
        preferred_relation,
        candidate_set,
        hierarchy,
        max_distance,
    )
