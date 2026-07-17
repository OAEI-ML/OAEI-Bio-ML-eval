"""
oaei_bioml_eval.coherence.report: file -> coherence metric-dict entry points + the
reasoner orchestrator (the HermiT-timeout -> ELK gate).

`score_global_coherence_files` composes a global `=` alignment over both ontology
views, classifies it, and returns the unsatisfiable-class count + the degree of
INCOHERENCE (0 = clean, higher = worse). `score_local_coherence_files` does the
same over each query's rank-1 committed `(src, top1)` mapping and returns the mean
per-query incoherence. `score_structural_proxy_files` is the dependency-free
participant proxy (a STUB until its rules are fixed — never the leaderboard value).

One exact shared composite is built without materialization and reasoned over by
both HermiT and ELK so the denominator + axioms are identical; every
unsatisfiable IRI is asserted to lie in its signature (numerator subset of
denominator).
"""
from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias, TypeVar, cast

if TYPE_CHECKING:
    from pyowl_core import (
        IRI,
        CancellationToken,
        ImportResolver,
        LoadOptions,
        OntologyInput,
        OntologyView,
    )
else:
    IRI = CancellationToken = ImportResolver = LoadOptions = OntologyInput = OntologyView = object

from ..io import write_json
from . import structural
from .bridge import (
    Correspondence,
    analyze_correspondences,
    coerce_ontology_pair_once,
    compose_alignment_views,
    invalid_alignment_iris,
    named_class_iris,
)
from .loaders import load_committed_top1, load_global_pairs, load_relation_typed_correspondences
from .metrics import global_coherence_ratio, local_coherence_aggregate
from .native_reasoners import HermiTTimeoutError
from .provenance import build_coherence_provenance
from .reasoner import CoherenceReasoner, UnsatResult, load_reasoner

ReasonerName = Literal["hermit", "elk"]
MetricValue: TypeAlias = int | float | str | bool | dict[str, object]
MetricReport: TypeAlias = dict[str, MetricValue]
CorrespondenceT = TypeVar("CorrespondenceT", bound=tuple[str, ...])


def _validated_pairs(
    pairs: Iterable[CorrespondenceT], source: object, *, skip_invalid: bool
) -> tuple[list[CorrespondenceT], int]:
    """
    reject (or, if skip_invalid, drop) correspondences whose IRIs are malformed —
    embedded whitespace/control means several IRIs run together (a corrupted alignment,
    or a malformed/truncated input OWL). caught here so the failure is a clear message,
    not a cryptic parser failure after ontology composition.
    """
    pairs = list(pairs)
    bad = invalid_alignment_iris(cast(Iterable[tuple[str, str]], pairs))
    if not bad:
        return pairs, 0
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
    retained: list[CorrespondenceT] = []
    for item in pairs:
        slots = tuple(item)
        if slots[0] not in bad_set and slots[1] not in bad_set:
            retained.append(item)
    return retained, len(pairs) - len(retained)


def _classify_view_with_gate(
    reasoner: CoherenceReasoner,
    ontology: OntologyView,
    *,
    prefer: ReasonerName,
    timeout_s: float | None,
) -> UnsatResult:
    """Use the same exact composite for the preferred and timeout-fallback pass."""

    try:
        return reasoner.unsatisfiable_classes_view(
            ontology, which=prefer, timeout_s=timeout_s
        )
    except HermiTTimeoutError as error:
        if prefer == "hermit":
            fallback = reasoner.unsatisfiable_classes_view(
                ontology, which="elk", timeout_s=timeout_s
            )
            provenance = dict(fallback.provenance)
            if error.attempt:
                provenance["attempts"] = (dict(error.attempt),)
            return replace(
                fallback,
                elapsed_seconds=error.elapsed_seconds + fallback.elapsed_seconds,
                fallback_reason="hermit-timeout",
                provenance=provenance,
            )
        raise


def _coherence_over_pairs(
    reasoner: CoherenceReasoner,
    source: OntologyView,
    target: OntologyView,
    pairs: Iterable[object],
    *,
    prefer: ReasonerName,
    timeout_s: float | None,
) -> tuple[tuple[str, ...], UnsatResult, OntologyView]:
    """Compose once, classify once, and enforce result integrity."""

    merged_view = compose_alignment_views(
        source,
        target,
        cast(Iterable[Correspondence], pairs),
    )
    denominator = named_class_iris(merged_view)
    result = _classify_view_with_gate(
        reasoner,
        merged_view,
        prefer=prefer,
        timeout_s=timeout_s,
    )
    stray = set(result.unsatisfiable) - set(denominator)
    if stray:   # numerator must lie within the merged signature
        raise AssertionError(f"unsatisfiable IRIs absent from the merged signature: {sorted(stray)}")
    return denominator, result, merged_view


def _global_report(
    rsnr: CoherenceReasoner,
    pairs: Iterable[Correspondence],
    source: OntologyView,
    target: OntologyView,
    *,
    reasoner: ReasonerName,
    timeout_s: float | None,
    invalid_dropped_count: int = 0,
    api_options: dict[str, object] | None = None,
    include_asserted_correspondences: bool = False,
) -> MetricReport:
    bridge = analyze_correspondences(
        pairs, invalid_dropped_count=invalid_dropped_count
    )
    denominator, result, merged_view = _coherence_over_pairs(
        rsnr,
        source,
        target,
        bridge.correspondences,
        prefer=reasoner,
        timeout_s=timeout_s,
    )
    union, count = len(denominator), len(result.unsatisfiable)
    metrics: MetricReport = {
        "global_coherence": global_coherence_ratio(count, union),
        "unsatisfiable_count": count,
        "union_class_count": union,
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
        "inconsistent": result.inconsistent,
    }
    metrics["provenance"] = build_coherence_provenance(
        merged_view,
        bridge,
        denominator,
        result,
        requested_reasoner=reasoner,
        timeout_s=timeout_s,
        api_options=api_options,
    )
    if include_asserted_correspondences:
        metrics["asserted_correspondences"] = len(bridge.correspondences)
    return metrics


def _local_report(
    rsnr: CoherenceReasoner,
    committed: list[tuple[str, str]],
    source: OntologyView,
    target: OntologyView,
    *,
    reasoner: ReasonerName,
    timeout_s: float | None,
    invalid_dropped_count: int = 0,
    api_options: dict[str, object] | None = None,
) -> MetricReport:
    if not committed:
        empty = local_coherence_aggregate([])
        empty_metrics: MetricReport = {
            "local_coherence": empty["local_coherence"],
            "local_coherence_queries": empty["local_coherence_queries"],
            "reasoner_used": reasoner,
            "lower_bound": reasoner == "elk",
            "inconsistent": False,
        }
        bridge = analyze_correspondences(
            (), invalid_dropped_count=invalid_dropped_count
        )
        empty_merged = compose_alignment_views(
            source, target, ()
        )
        denominator = named_class_iris(empty_merged)
        not_run = UnsatResult((), "not-run", 0.0)
        empty_metrics["provenance"] = build_coherence_provenance(
            empty_merged,
            bridge,
            denominator,
            not_run,
            requested_reasoner=reasoner,
            timeout_s=timeout_s,
            api_options=api_options,
        )
        return empty_metrics
    bridge = analyze_correspondences(
        committed, invalid_dropped_count=invalid_dropped_count
    )
    denominator, result, merged_view = _coherence_over_pairs(
        rsnr,
        source,
        target,
        bridge.correspondences,
        prefer=reasoner,
        timeout_s=timeout_s,
    )
    unsat = set(result.unsatisfiable)
    flags = [(src in unsat or tgt in unsat) for src, tgt in committed]
    metrics: MetricReport = {
        **local_coherence_aggregate(flags),
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
        "inconsistent": result.inconsistent,
    }
    metrics["provenance"] = build_coherence_provenance(
        merged_view,
        bridge,
        denominator,
        result,
        requested_reasoner=reasoner,
        timeout_s=timeout_s,
        api_options=api_options,
    )
    return metrics


def score_global_coherence(
    correspondences: Iterable[tuple[str, str]],
    source: OntologyView,
    target: OntologyView,
    *,
    reasoner: ReasonerName = "hermit",
    timeout_s: float | None = 7200.0,
    skip_invalid: bool = False,
) -> MetricReport:
    """Score a shared-view alignment's degree of incoherence (0 clean; higher worse).

    ``source`` and ``target`` are handed to the reasoner seam by exact object
    identity.  This snapshot-first function never calls ``coerce_snapshot``.
    """
    pairs, invalid_dropped = _validated_pairs(
        correspondences, "correspondences", skip_invalid=skip_invalid
    )
    rsnr = load_reasoner()
    return _global_report(
        rsnr,
        pairs,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "global", "skip_invalid": skip_invalid},
    )


def score_reference_coherence(
    correspondences: Iterable[Correspondence],
    source: OntologyView,
    target: OntologyView,
    *,
    reasoner: ReasonerName = "elk",
    timeout_s: float | None = 7200.0,
    skip_invalid: bool = False,
) -> MetricReport:
    """Score a repaired reference's degree of incoherence over exact shared views.

    Callers pass the retained relation-typed correspondences; repaired-reference
    file filtering remains in ``score_reference_coherence_files``.
    """
    typed, invalid_dropped = _validated_pairs(
        correspondences, "correspondences", skip_invalid=skip_invalid
    )
    rsnr = load_reasoner()
    return _global_report(
        rsnr,
        typed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "reference", "skip_invalid": skip_invalid},
        include_asserted_correspondences=True,
    )


def score_local_coherence(
    committed_top1: Iterable[tuple[str, str]],
    source: OntologyView,
    target: OntologyView,
    *,
    reasoner: ReasonerName = "hermit",
    timeout_s: float | None = 7200.0,
    skip_invalid: bool = False,
) -> MetricReport:
    """Score rank-1 mean incoherence over exact shared ontology views.

    Query occurrences are retained for the mean while the asserted bridge is
    deduplicated.  Queries without a commitment must be omitted by the caller.
    """
    committed, invalid_dropped = _validated_pairs(
        committed_top1, "committed_top1", skip_invalid=skip_invalid
    )
    rsnr = load_reasoner()
    return _local_report(
        rsnr,
        committed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "local", "skip_invalid": skip_invalid},
    )


def _coerce_ontology_pair(
    source: OntologyInput,
    target: OntologyInput,
    *,
    source_document_iri: IRI | str | None,
    target_document_iri: IRI | str | None,
    load_options: LoadOptions | None,
    resolver: ImportResolver | None,
    cancellation_token: CancellationToken | None,
) -> tuple[OntologyView, OntologyView]:
    return coerce_ontology_pair_once(
        source,
        target,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )


def score_global_coherence_files(
    submission_path: str | Path,
    src_owl: OntologyInput,
    tgt_owl: OntologyInput,
    *,
    reasoner: ReasonerName = "hermit",
    timeout_s: float | None = 7200.0,
    output_path: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
) -> MetricReport:
    """Load each ontology once, then score degree of incoherence (0 clean; higher worse)."""
    pairs, invalid_dropped = _validated_pairs(
        load_global_pairs(submission_path), submission_path, skip_invalid=skip_invalid
    )
    rsnr = load_reasoner()
    source, target = _coerce_ontology_pair(
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    metrics = _global_report(
        rsnr,
        pairs,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "global", "skip_invalid": skip_invalid},
    )
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_reference_coherence_files(
    reference_path: str | Path,
    src_owl: OntologyInput,
    tgt_owl: OntologyInput,
    *,
    reasoner: ReasonerName = "elk",
    timeout_s: float | None = 7200.0,
    output_path: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
) -> MetricReport:
    """Load once and score a filtered repaired reference's degree of incoherence."""
    typed, invalid_dropped = _validated_pairs(
        load_relation_typed_correspondences(reference_path),
        reference_path,
        skip_invalid=skip_invalid,
    )
    rsnr = load_reasoner()
    source, target = _coerce_ontology_pair(
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    metrics = _global_report(
        rsnr,
        typed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "reference", "skip_invalid": skip_invalid},
        include_asserted_correspondences=True,
    )
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_local_coherence_files(
    ranked_path: str | Path,
    src_owl: OntologyInput,
    tgt_owl: OntologyInput,
    *,
    reasoner: ReasonerName = "hermit",
    timeout_s: float | None = 7200.0,
    output_path: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
) -> MetricReport:
    """Load once and score rank-1 mean incoherence with batched blame semantics."""
    committed, invalid_dropped = _validated_pairs(
        load_committed_top1(ranked_path), ranked_path, skip_invalid=skip_invalid
    )
    rsnr = load_reasoner()
    source, target = _coerce_ontology_pair(
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    metrics = _local_report(
        rsnr,
        committed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        invalid_dropped_count=invalid_dropped,
        api_options={"metric": "local", "skip_invalid": skip_invalid},
    )
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics


def score_structural_proxy_files(
    *args: object,
    output_path: str | Path | None = None,
    **kwargs: object,
) -> MetricReport:
    """the dependency-free structural proxy (STUB until the rule set is fixed) — never the leaderboard value"""
    metrics = cast(MetricReport, structural.structural_coherence_proxy(*args, **kwargs))
    if output_path is not None:
        write_json(output_path, metrics)
    return metrics
