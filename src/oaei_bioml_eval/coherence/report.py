"""
oaei_bioml_eval.coherence.report: file -> coherence metric-dict entry points + the
reasoner orchestrator (the HermiT-timeout -> ELK gate, backend-independent).

`score_global_coherence_files` merges a global `=` alignment with both OWLs,
classifies, and returns the unsatisfiable-class count + the degree of INCOHERENCE
(0 = clean, higher = worse). `score_local_coherence_files` does the same over each 
query's rank-1 committed `(src, top1)` mapping and returns the mean per-query 
incoherence. `score_structural_proxy_files` is the dependency-free participant 
proxy (a STUB until its rules are fixed — never the leaderboard value).

The merged ontology is built ONCE and reasoned over by both HermiT and ELK so the
denominator + axioms are identical; every unsatisfiable IRI is asserted to lie in
the merged signature (numerator subset of denominator).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..io import write_json
from . import structural
from .loaders import load_committed_top1, load_global_pairs, load_relation_typed_correspondences
from .metrics import global_coherence_ratio, local_coherence_aggregate
from .reasoner import CoherenceReasoner, UnsatResult, invalid_alignment_iris, load_reasoner


def _validated_pairs(pairs, source, *, skip_invalid: bool) -> list[tuple[str, str]]:
    """
    reject (or, if skip_invalid, drop) correspondences whose IRIs are malformed —
    embedded whitespace/control means several IRIs run together (a corrupted alignment,
    or a malformed/truncated input OWL). caught here so the failure is a clear message,
    not a cryptic ROBOT "invalid characters" abort mid-merge.
    """
    pairs = list(pairs)
    bad = invalid_alignment_iris(pairs)
    if not bad:
        return pairs
    preview = "\n".join("  " + repr(iri) for iri in bad[:5])
    summary = (f"{len(bad)} alignment IRI(s) in {source} contain embedded whitespace/control characters "
               f"— they look like multiple IRIs concatenated (a corrupted alignment, or a malformed/"
               f"truncated input OWL). First:\n{preview}")
    if not skip_invalid:
        raise ValueError(summary + "\n\nFix the alignment/ontology, or pass skip_invalid=True "
                         "(--skip-invalid-iris) to drop these correspondences and score the rest.")
    bad_set = set(bad)
    print(f"[coherence] WARNING: {summary}\n -> dropping these correspondences (skip_invalid).", file=sys.stderr)
    # preserve each item's arity (2-tuple `=` pair or 3-tuple `(s,t,rel)`) — only the IRI slots gate
    return [item for item in pairs if tuple(item)[0] not in bad_set and tuple(item)[1] not in bad_set]


def _classify_with_gate(reasoner: CoherenceReasoner, merged, *, prefer: str, timeout_s: float | None) -> UnsatResult:
    """run `prefer`; on a HermiT wall-clock timeout fall back to ELK (EL <= DL -> a valid `>=`)"""
    try:
        return reasoner.unsatisfiable_classes(merged, which=prefer, timeout_s=timeout_s)
    except TimeoutError:
        if prefer == "hermit":
            return reasoner.unsatisfiable_classes(merged, which="elk", timeout_s=timeout_s)
        raise


def _coherence_over_pairs(reasoner, src_owl, tgt_owl, pairs, *, prefer, timeout_s):
    """merge ONCE, take the signature, classify under the gate; assert numerator subset of denominator"""
    merged = reasoner.merge(Path(src_owl), Path(tgt_owl), pairs)
    try:
        denominator = reasoner.named_classes(merged)
        result = _classify_with_gate(reasoner, merged, prefer=prefer, timeout_s=timeout_s)
    finally:
        reasoner.dispose(merged)
    stray = set(result.unsatisfiable) - set(denominator)
    if stray:   # numerator must lie within the merged signature
        raise AssertionError(f"unsatisfiable IRIs absent from the merged signature: {sorted(stray)}")
    return denominator, result


def score_global_coherence_files(
    submission_path: str | Path,
    src_owl: str | Path,
    tgt_owl: str | Path,
    *,
    reasoner: str = "hermit",
    timeout_s: float = 7200.0,
    output_path: str | Path | None = None,
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    **reasoner_kwargs,
) -> dict:
    """global alignment + both OWLs -> unsatisfiable_count + the degree of incoherence"""
    pairs = _validated_pairs(load_global_pairs(submission_path), submission_path, skip_invalid=skip_invalid)
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    denominator, result = _coherence_over_pairs(
        rsnr, src_owl, tgt_owl, pairs, prefer=reasoner, timeout_s=timeout_s
    )
    union, count = len(denominator), len(result.unsatisfiable)
    metrics = {
        "global_coherence": global_coherence_ratio(count, union),
        "unsatisfiable_count": count,
        "union_class_count": union,
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
    }
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_reference_coherence_files(
    reference_path: str | Path,
    src_owl: str | Path,
    tgt_owl: str | Path,
    *,
    reasoner: str = "elk",
    timeout_s: float = 7200.0,
    output_path: str | Path | None = None,
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    **reasoner_kwargs,
) -> dict:
    """
    coherence of an Option-Two repaired REFERENCE: its kept `=`/`<=`/`>=` correspondences are
    asserted as EquivalentClasses / SubClassOf (the bridge mirrors CoherenceCheckELK); `?`-flagged
    cells are dropped (not asserted). Same merge/classify path as the submission scorer, so the
    denominator + unsatisfiable count are directly comparable. Defaults to ELK (the EL `>=` lower
    bound used for the SNOMED-scale references).
    """
    typed = _validated_pairs(load_relation_typed_correspondences(reference_path),
                             reference_path, skip_invalid=skip_invalid)
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    denominator, result = _coherence_over_pairs(
        rsnr, src_owl, tgt_owl, typed, prefer=reasoner, timeout_s=timeout_s
    )
    union, count = len(denominator), len(result.unsatisfiable)
    metrics = {
        "global_coherence": global_coherence_ratio(count, union),
        "unsatisfiable_count": count,
        "union_class_count": union,
        "asserted_correspondences": len(typed),
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
    }
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_local_coherence_files(
    ranked_path: str | Path,
    src_owl: str | Path,
    tgt_owl: str | Path,
    *,
    reasoner: str = "hermit",
    timeout_s: float = 7200.0,
    output_path: str | Path | None = None,
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    **reasoner_kwargs,
) -> dict:
    """
    rank-1 committed mappings + both OWLs -> mean per-query incoherence. one batched
    classification of the committed alignment; a query is incoherent iff its src or
    top1 is unsatisfiable. (OPEN: per-query vs this batched blame; confirm with Jon.)
    """
    committed = _validated_pairs(load_committed_top1(ranked_path), ranked_path, skip_invalid=skip_invalid)
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    if not committed:
        result_used, lower = reasoner, reasoner == "elk"
        metrics = {**local_coherence_aggregate([]), "reasoner_used": result_used, "lower_bound": lower}
    else:
        _denominator, result = _coherence_over_pairs(
            rsnr, src_owl, tgt_owl, sorted(set(committed)), prefer=reasoner, timeout_s=timeout_s
        )
        unsat = set(result.unsatisfiable)
        flags = [(src in unsat or tgt in unsat) for src, tgt in committed]
        metrics = {**local_coherence_aggregate(flags),
                   "reasoner_used": result.reasoner_used,
                   "lower_bound": result.reasoner_used == "elk"}
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_structural_proxy_files(*args, output_path: str | Path | None = None, **kwargs) -> dict:
    """the dependency-free structural proxy (STUB until the rule set is fixed) — never the leaderboard value"""
    metrics = structural.structural_coherence_proxy(*args, **kwargs)
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics
