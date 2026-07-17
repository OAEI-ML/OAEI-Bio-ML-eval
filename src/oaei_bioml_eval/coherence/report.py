"""
oaei_bioml_eval.coherence.report: file -> coherence metric-dict entry points + the
reasoner orchestrator (the HermiT-timeout -> ELK gate, backend-independent).

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

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypeVar, cast

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
    SnapshotCompatibilityError,
    coerce_ontology_pair_once,
    compose_alignment_views,
    invalid_alignment_iris,
    named_class_iris,
    normalize_correspondences,
)
from .loaders import load_committed_top1, load_global_pairs, load_relation_typed_correspondences
from .metrics import global_coherence_ratio, local_coherence_aggregate
from .reasoner import (
    CoherenceReasoner,
    MergedOntology,
    UnsatResult,
    load_reasoner,
)

ReasonerName = Literal["hermit", "elk"]
MetricValue: TypeAlias = int | float | str | bool
MetricReport: TypeAlias = dict[str, MetricValue]
CorrespondenceT = TypeVar("CorrespondenceT", bound=tuple[str, ...])


def _validated_pairs(
    pairs: Iterable[CorrespondenceT], source: object, *, skip_invalid: bool
) -> list[CorrespondenceT]:
    """
    reject (or, if skip_invalid, drop) correspondences whose IRIs are malformed —
    embedded whitespace/control means several IRIs run together (a corrupted alignment,
    or a malformed/truncated input OWL). caught here so the failure is a clear message,
    not a cryptic ROBOT "invalid characters" abort mid-merge.
    """
    pairs = list(pairs)
    bad = invalid_alignment_iris(cast(Iterable[tuple[str, str]], pairs))
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


def _classify_with_gate(
    reasoner: CoherenceReasoner,
    merged: MergedOntology,
    *,
    prefer: ReasonerName,
    timeout_s: float | None,
) -> UnsatResult:
    """run `prefer`; on a HermiT wall-clock timeout fall back to ELK (EL <= DL -> a valid `>=`)"""
    try:
        return reasoner.unsatisfiable_classes(merged, which=prefer, timeout_s=timeout_s)
    except TimeoutError:
        if prefer == "hermit":
            return reasoner.unsatisfiable_classes(merged, which="elk", timeout_s=timeout_s)
        raise


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
    except TimeoutError:
        if prefer == "hermit":
            return reasoner.unsatisfiable_classes_view(
                ontology, which="elk", timeout_s=timeout_s
            )
        raise


def _coherence_over_pairs(
    reasoner: CoherenceReasoner,
    source: object,
    target: object,
    pairs: Iterable[object],
    *,
    prefer: ReasonerName,
    timeout_s: float | None,
    shared_views: bool = False,
) -> tuple[tuple[str, ...], UnsatResult]:
    """Compose/merge once, classify once, and enforce result integrity.

    The shared branch owns the only official O2 bridge: a core delta over one
    zero-copy composite. The path branch is retained only for quarantined Java
    differential captures and disappears in O4.
    """
    if shared_views:
        merged_view = compose_alignment_views(
            cast(OntologyView, source),
            cast(OntologyView, target),
            cast(Iterable[Correspondence], pairs),
        )
        denominator = named_class_iris(merged_view)
        result = _classify_view_with_gate(
            reasoner,
            merged_view,
            prefer=prefer,
            timeout_s=timeout_s,
        )
    else:
        if not isinstance(source, (str, os.PathLike)) or not isinstance(
            target, (str, os.PathLike)
        ):
            raise SnapshotCompatibilityError(
                "legacy differential backends accept ontology paths only"
            )
        merged = reasoner.merge(Path(source), Path(target), pairs)
        try:
            denominator = reasoner.named_classes(merged)
            result = _classify_with_gate(
                reasoner, merged, prefer=prefer, timeout_s=timeout_s
            )
        finally:
            reasoner.dispose(merged)
    stray = set(result.unsatisfiable) - set(denominator)
    if stray:   # numerator must lie within the merged signature
        raise AssertionError(f"unsatisfiable IRIs absent from the merged signature: {sorted(stray)}")
    return denominator, result


def _require_shared_view_reasoner(reasoner: CoherenceReasoner) -> None:
    if not reasoner.accepts_ontology_views:
        raise SnapshotCompatibilityError(
            f"{type(reasoner).__name__} is the quarantined file-only differential oracle; "
            "snapshot-first coherence requires a native shared-view adapter"
        )


def _global_report(
    rsnr: CoherenceReasoner,
    pairs: Iterable[Correspondence],
    source: object,
    target: object,
    *,
    reasoner: ReasonerName,
    timeout_s: float | None,
    shared_views: bool,
    include_asserted_correspondences: bool = False,
) -> MetricReport:
    normalized = normalize_correspondences(pairs)
    denominator, result = _coherence_over_pairs(
        rsnr,
        source,
        target,
        normalized,
        prefer=reasoner,
        timeout_s=timeout_s,
        shared_views=shared_views,
    )
    union, count = len(denominator), len(result.unsatisfiable)
    metrics: MetricReport = {
        "global_coherence": global_coherence_ratio(count, union),
        "unsatisfiable_count": count,
        "union_class_count": union,
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
    }
    if include_asserted_correspondences:
        metrics["asserted_correspondences"] = len(normalized)
    return metrics


def _local_report(
    rsnr: CoherenceReasoner,
    committed: list[tuple[str, str]],
    source: object,
    target: object,
    *,
    reasoner: ReasonerName,
    timeout_s: float | None,
    shared_views: bool,
) -> MetricReport:
    if not committed:
        empty = local_coherence_aggregate([])
        return {
            "local_coherence": empty["local_coherence"],
            "local_coherence_queries": empty["local_coherence_queries"],
            "reasoner_used": reasoner,
            "lower_bound": reasoner == "elk",
        }
    normalized = normalize_correspondences(committed)
    _denominator, result = _coherence_over_pairs(
        rsnr,
        source,
        target,
        normalized,
        prefer=reasoner,
        timeout_s=timeout_s,
        shared_views=shared_views,
    )
    unsat = set(result.unsatisfiable)
    flags = [(src in unsat or tgt in unsat) for src, tgt in committed]
    return {
        **local_coherence_aggregate(flags),
        "reasoner_used": result.reasoner_used,
        "lower_bound": result.reasoner_used == "elk",
    }


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
    pairs = _validated_pairs(correspondences, "correspondences", skip_invalid=skip_invalid)
    rsnr = load_reasoner()
    _require_shared_view_reasoner(rsnr)
    return _global_report(
        rsnr,
        pairs,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=True,
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
    typed = _validated_pairs(correspondences, "correspondences", skip_invalid=skip_invalid)
    rsnr = load_reasoner()
    _require_shared_view_reasoner(rsnr)
    return _global_report(
        rsnr,
        typed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=True,
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
    committed = _validated_pairs(committed_top1, "committed_top1", skip_invalid=skip_invalid)
    rsnr = load_reasoner()
    _require_shared_view_reasoner(rsnr)
    return _local_report(
        rsnr,
        committed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=True,
    )


def _coerce_for_shared_backend(
    rsnr: CoherenceReasoner,
    source: OntologyInput,
    target: OntologyInput,
    *,
    source_document_iri: IRI | str | None,
    target_document_iri: IRI | str | None,
    load_options: LoadOptions | None,
    resolver: ImportResolver | None,
    cancellation_token: CancellationToken | None,
) -> tuple[object, object] | None:
    if not rsnr.accepts_ontology_views:
        return None
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
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
    **reasoner_kwargs: Any,
) -> MetricReport:
    """Load each ontology once, then score degree of incoherence (0 clean; higher worse).

    The full ``pyowl_core.OntologyInput`` surface is accepted when a shared-view
    adapter is active.  Legacy Java backends retain path-only behavior solely for
    differential capture during O1.
    """
    pairs = _validated_pairs(
        load_global_pairs(submission_path), submission_path, skip_invalid=skip_invalid
    )
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    views = _coerce_for_shared_backend(
        rsnr,
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    shared = views is not None
    source, target = views if views is not None else (src_owl, tgt_owl)
    metrics = _global_report(
        rsnr,
        pairs,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=shared,
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
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
    **reasoner_kwargs: Any,
) -> MetricReport:
    """Load once and score a filtered repaired reference's degree of incoherence."""
    typed = _validated_pairs(
        load_relation_typed_correspondences(reference_path),
        reference_path,
        skip_invalid=skip_invalid,
    )
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    views = _coerce_for_shared_backend(
        rsnr,
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    shared = views is not None
    source, target = views if views is not None else (src_owl, tgt_owl)
    metrics = _global_report(
        rsnr,
        typed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=shared,
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
    backend: str | None = None,
    robot_jar: str | Path | None = None,
    skip_invalid: bool = False,
    source_document_iri: IRI | str | None = None,
    target_document_iri: IRI | str | None = None,
    load_options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
    cancellation_token: CancellationToken | None = None,
    **reasoner_kwargs: Any,
) -> MetricReport:
    """Load once and score rank-1 mean incoherence with batched blame semantics."""
    committed = _validated_pairs(
        load_committed_top1(ranked_path), ranked_path, skip_invalid=skip_invalid
    )
    rsnr = load_reasoner(backend, robot_jar=robot_jar, **reasoner_kwargs)
    views = _coerce_for_shared_backend(
        rsnr,
        src_owl,
        tgt_owl,
        source_document_iri=source_document_iri,
        target_document_iri=target_document_iri,
        load_options=load_options,
        resolver=resolver,
        cancellation_token=cancellation_token,
    )
    shared = views is not None
    source, target = views if views is not None else (src_owl, tgt_owl)
    metrics = _local_report(
        rsnr,
        committed,
        source,
        target,
        reasoner=reasoner,
        timeout_s=timeout_s,
        shared_views=shared,
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
