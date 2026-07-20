#!/usr/bin/env python3
"""Measure and compare the complete Java-free O3 shared-view reasoner pipeline."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import multiprocessing
import resource
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, TypeAlias, TypeVar

import pyowl_core

from oaei_bioml_eval.coherence.bridge import (
    analyze_correspondences,
    compose_alignment_views,
    named_class_iris,
)
from oaei_bioml_eval.coherence.native_reasoners import _backend_metadata
from oaei_bioml_eval.coherence.provenance import (
    build_coherence_provenance,
    canonical_provenance_json,
    sorted_line_sha256,
)
from oaei_bioml_eval.coherence.reasoner import UnsatResult

T = TypeVar("T")
OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"

SINGLE_RUN_SCHEMA = "oaei-bioml-eval.o3-native-benchmark/2"
ACCEPTANCE_SCHEMA = "oaei-bioml-eval.o3-native-acceptance/1"
WALL_REGRESSION_LIMIT = 0.25
RSS_REGRESSION_LIMIT = 0.20
MIN_ACCEPTANCE_WARMUPS = 1
MIN_ACCEPTANCE_REPETITIONS = 3
_MAX_SAMPLE_RESPONSE_BYTES = 16 * 1024 * 1024
_SAMPLE_SHUTDOWN_SECONDS = 2.0
_PIPELINE_WALL_PHASES = (
    "source_load",
    "target_load",
    "bridge_normalization",
    "composition",
    "signature",
    "reasoning_total",
    "reporting",
)
_SCALAR_INGESTION_PATHS = frozenset({"scalar-python", "scalar-native", "scalar-wire"})
_REQUIRED_PUBLIC_COUNTERS = (
    "base_flattening_bytes",
    "parser_calls",
    "per_row_ffi_calls",
    "resolver_calls",
    "wire_decoder_calls",
    "wire_encoder_calls",
)
_MATERIALIZATION_COUNTERS = (
    "materialized_scalar_rows",
    "scalar_axiom_materializations",
)
_COPY_COUNTERS = ("encoded_staging_copy_bytes", "structural_copy_bytes")
_FORBIDDEN_ZERO_COUNTERS = frozenset(
    {
        *_REQUIRED_PUBLIC_COUNTERS,
        *_MATERIALIZATION_COUNTERS,
        "encoded_private_ir_bytes",
        "scalar_term_materializations",
        "structural_copy_bytes",
    }
)


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _measure(function: Callable[[], T]) -> tuple[T, float, float, int]:
    rss_before = _peak_rss_bytes()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    value = function()
    cpu_elapsed = time.process_time() - cpu_started
    wall_elapsed = time.perf_counter() - wall_started
    return value, wall_elapsed, cpu_elapsed, max(0, _peak_rss_bytes() - rss_before)


def _ontology_bytes(class_count: int, *, source: bool) -> bytes:
    declarations: list[str] = []
    disjoint: list[str] = []
    for index in range(class_count):
        source_iri = f"urn:oaei:benchmark:source:{index}"
        target_iri = f"urn:oaei:benchmark:target:{index}"
        declarations.append(f"Declaration(Class(<{source_iri if source else target_iri}>))")
        if source:
            disjoint.append(f"DisjointClasses(<{source_iri}> <{target_iri}>)")
    ontology_iri = "urn:oaei:benchmark:source" if source else "urn:oaei:benchmark:target"
    return f"Ontology(<{ontology_iri}> {' '.join(declarations)} {' '.join(disjoint)})".encode()


def _entity_iris(values: Iterable[object]) -> tuple[str, ...]:
    iris = {
        value
        for entity in values
        if isinstance(value := getattr(getattr(entity, "iri", None), "value", None), str)
        and value not in {OWL_THING, OWL_NOTHING}
    }
    return tuple(sorted(iris))


def _package_version(module: object, distribution: str) -> str:
    version = getattr(module, "__version__", None)
    if isinstance(version, str) and version:
        return version
    return importlib.metadata.version(distribution)


def _retains_identity(root: object, expected: object) -> bool:
    """Inspect only public composition/overlay ownership links."""

    pending = [root]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is expected:
            return True
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        base = getattr(current, "base", None)
        if base is not None:
            pending.append(base)
        for member in getattr(current, "members", ()):
            pending.append(getattr(member, "view", member))
    return False


def _reason(
    ontology: object,
    denominator: tuple[str, ...],
    *,
    reasoner: str,
    backend: str,
    timeout_s: float | None,
) -> tuple[
    tuple[str, ...],
    bool,
    Mapping[str, object],
    dict[str, float],
    dict[str, float],
    dict[str, int],
]:
    wall: dict[str, float] = {}
    cpu: dict[str, float] = {}
    rss: dict[str, int] = {}
    if reasoner == "elk":
        module = importlib.import_module("pyelk")
        if backend not in {"auto", "python", "rust"}:
            raise ValueError("pyELK backend must be auto, python, or rust")
        config = module.ReasonerConfig(backend=backend)
        (
            session,
            wall["reasoner_compile"],
            cpu["reasoner_compile"],
            rss["reasoner_compile"],
        ) = _measure(lambda: module.Reasoner(ontology, config))
        try:
            if session.ontology is not ontology:
                raise RuntimeError("pyELK did not retain the supplied composite by identity")
            consistency, wall["consistency"], cpu["consistency"], rss["consistency"] = _measure(
                session.is_consistent
            )
            consistent = consistency.value
            taxonomy, wall["classification"], cpu["classification"], rss["classification"] = (
                _measure(session.classify)
            )
            unsatisfiable = (
                denominator if not consistent else _entity_iris(taxonomy.value.bottom.members)
            )
            profile = {
                "complete": taxonomy.complete,
                "reasons": [
                    {
                        "task": issue.task.value,
                        "features": list(issue.features),
                        "constructors": list(issue.constructors),
                        "polarities": list(issue.polarities),
                    }
                    for issue in taxonomy.reasons
                ],
            }
            package_version = _package_version(module, "pyelk-reasoner")
            metadata = {
                "schema": "native-reasoner-provenance/1",
                "package": "pyELK",
                "package_version": package_version,
                "backend": _backend_metadata(session, package_version),
                "transport": {
                    "mode": "in-process-identity",
                    "ontology_identity": True,
                    "wire_encode_calls": 0,
                },
                "profile": profile,
            }
            return unsatisfiable, not consistent, metadata, wall, cpu, rss
        finally:
            session.close()

    module = importlib.import_module("pyhermit")
    if backend not in {"auto", "python", "native", "verify"}:
        raise ValueError("pyHermiT backend must be auto, python, native, or verify")
    config = module.ReasonerConfig(backend=backend, timeout=timeout_s)
    (
        session,
        wall["reasoner_compile"],
        cpu["reasoner_compile"],
        rss["reasoner_compile"],
    ) = _measure(lambda: module.Reasoner(ontology, config=config))
    try:
        if session.ontology is not ontology:
            raise RuntimeError("pyHermiT did not retain the supplied composite by identity")
        consistent, wall["consistency"], cpu["consistency"], rss["consistency"] = _measure(
            session.is_consistent
        )
        if consistent:
            values, wall["classification"], cpu["classification"], rss["classification"] = _measure(
                session.unsatisfiable_classes
            )
            unsatisfiable = _entity_iris(values)
        else:
            wall["classification"] = 0.0
            cpu["classification"] = 0.0
            rss["classification"] = 0
            unsatisfiable = denominator
        package_version = _package_version(module, "pyHermiT")
        metadata = {
            "schema": "native-reasoner-provenance/1",
            "package": "pyHermiT",
            "package_version": package_version,
            "backend": _backend_metadata(session, package_version),
            "transport": {
                "mode": "in-process-identity",
                "ontology_identity": True,
                "wire_encode_calls": 0,
            },
            "profile": {"complete": True, "reasons": []},
        }
        return unsatisfiable, not consistent, metadata, wall, cpu, rss
    finally:
        session.dispose()


def _is_zero_counter(value: object) -> bool:
    return value is False or (isinstance(value, int) and not isinstance(value, bool) and value == 0)


def _counter_evidence(
    compiler_handoff: Mapping[str, object],
) -> dict[str, object]:
    public_counters = compiler_handoff.get("counters")
    counters = dict(public_counters) if isinstance(public_counters, Mapping) else {}
    ingestion_path = compiler_handoff.get("ingestion_path")
    missing_public_counters = [name for name in _REQUIRED_PUBLIC_COUNTERS if name not in counters]
    missing_public_counter_groups = [
        label
        for label, alternatives in (
            ("scalar_materialization", _MATERIALIZATION_COUNTERS),
            ("structural_copy", _COPY_COUNTERS),
        )
        if not any(name in counters for name in alternatives)
    ]
    complete_counter_coverage = (
        ingestion_path == "encoded-native"
        and not missing_public_counters
        and not missing_public_counter_groups
    )
    forbidden_values = {
        name: value for name, value in sorted(counters.items()) if name in _FORBIDDEN_ZERO_COUNTERS
    }
    nonzero_forbidden = {
        name: value for name, value in forbidden_values.items() if not _is_zero_counter(value)
    }
    return {
        "public_counters": counters,
        "materialized_scalar_rows": counters.get("materialized_scalar_rows"),
        "encoded_staging_copy_bytes": counters.get("encoded_staging_copy_bytes"),
        "copied_structural_bytes": counters.get("structural_copy_bytes"),
        "complete_public_counter_coverage": complete_counter_coverage,
        "missing_public_counters": missing_public_counters,
        "missing_public_counter_groups": missing_public_counter_groups,
        "forbidden_public_counters": forbidden_values,
        "nonzero_forbidden_public_counters": nonzero_forbidden,
        "zero_forbidden_public_counters": (complete_counter_coverage and not nonzero_forbidden),
        "benchmark_wire_encode_calls": 0,
        "selected_ingestion_path": ingestion_path,
    }


def run(
    class_count: int,
    bridge_count: int,
    *,
    reasoner: str,
    backend: str,
    timeout_s: float | None,
) -> dict[str, object]:
    wall: dict[str, float] = {}
    cpu: dict[str, float] = {}
    rss: dict[str, int] = {}
    source_bytes = _ontology_bytes(class_count, source=True)
    target_bytes = _ontology_bytes(class_count, source=False)
    source, wall["source_load"], cpu["source_load"], rss["source_load"] = _measure(
        lambda: pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:benchmark:source-document",
        )
    )
    target, wall["target_load"], cpu["target_load"], rss["target_load"] = _measure(
        lambda: pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:benchmark:target-document",
        )
    )
    pairs = tuple(
        (
            f"urn:oaei:benchmark:source:{index}",
            f"urn:oaei:benchmark:target:{index}",
        )
        for index in range(bridge_count)
    )
    (
        bridge,
        wall["bridge_normalization"],
        cpu["bridge_normalization"],
        rss["bridge_normalization"],
    ) = _measure(lambda: analyze_correspondences(pairs))
    composite, wall["composition"], cpu["composition"], rss["composition"] = _measure(
        lambda: compose_alignment_views(source, target, bridge.correspondences)
    )
    if not _retains_identity(composite, source) or not _retains_identity(composite, target):
        raise RuntimeError("composition did not retain both source views by identity")
    denominator, wall["signature"], cpu["signature"], rss["signature"] = _measure(
        lambda: named_class_iris(composite)
    )
    (
        reasoned,
        wall["reasoning_total"],
        cpu["reasoning_total"],
        rss["reasoning_total"],
    ) = _measure(
        lambda: _reason(
            composite,
            denominator,
            reasoner=reasoner,
            backend=backend,
            timeout_s=timeout_s,
        )
    )
    (
        unsatisfiable,
        inconsistent,
        reasoner_metadata,
        split_wall,
        split_cpu,
        split_rss,
    ) = reasoned
    wall.update(split_wall)
    cpu.update(split_cpu)
    rss.update(split_rss)
    result = UnsatResult(
        unsatisfiable,
        reasoner,
        sum(split_wall.values()),
        provenance=reasoner_metadata,
        inconsistent=inconsistent,
    )
    provenance, wall["reporting"], cpu["reporting"], rss["reporting"] = _measure(
        lambda: build_coherence_provenance(
            composite,
            bridge,
            denominator,
            result,
            requested_reasoner=reasoner,
            timeout_s=timeout_s,
            api_options={"benchmark": True, "backend": backend},
        )
    )
    canonical_provenance = canonical_provenance_json(provenance).encode()
    compiler_handoff = provenance["compiler_handoff"]
    if not isinstance(compiler_handoff, Mapping):
        raise RuntimeError("provenance omitted compiler handoff evidence")
    return {
        "schema": SINGLE_RUN_SCHEMA,
        "python": sys.version.split()[0],
        "core": pyowl_core.__version__,
        "reasoner": reasoner,
        "backend": reasoner_metadata["backend"],
        "class_count_per_side": class_count,
        "bridge_count": bridge_count,
        "denominator_count": len(denominator),
        "unsatisfiable_count": len(unsatisfiable),
        "inconsistent": inconsistent,
        "inputs": {
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "target_sha256": hashlib.sha256(target_bytes).hexdigest(),
            "bridge_sha256": bridge.fingerprint,
        },
        "identity": {
            "composite_retains_source": True,
            "composite_retains_target": True,
            "reasoner_retains_composite": True,
        },
        "compiler_handoff": dict(compiler_handoff),
        "materialization_and_copy": _counter_evidence(compiler_handoff),
        "results": {
            "denominator_sha256": sorted_line_sha256(denominator),
            "unsatisfiable_sha256": sorted_line_sha256(unsatisfiable),
            "composite_structural_fingerprint": composite.structural_fingerprint.hex,
            "provenance_sha256": hashlib.sha256(canonical_provenance).hexdigest(),
        },
        "provenance_bytes": len(canonical_provenance),
        "seconds": wall,
        "cpu_seconds": cpu,
        "peak_rss_increment_bytes": rss,
        "process_peak_rss_bytes": _peak_rss_bytes(),
    }


@dataclass(frozen=True, slots=True)
class SampleRequest:
    """One benchmark sample executed in a fresh process for independent peak RSS."""

    class_count: int
    bridge_count: int
    reasoner: str
    backend: str
    timeout_s: float | None


SampleRunner: TypeAlias = Callable[[SampleRequest], dict[str, object]]
SampleEntrypoint: TypeAlias = Callable[[Connection, SampleRequest], None]


def _sample_worker_entry(connection: Connection, request: SampleRequest) -> None:
    try:
        response: dict[str, object] = {
            "ok": True,
            "result": run(
                request.class_count,
                request.bridge_count,
                reasoner=request.reasoner,
                backend=request.backend,
                timeout_s=request.timeout_s,
            ),
        }
    except BaseException as error:
        response = {
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
    try:
        connection.send_bytes(
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    finally:
        connection.close()


def _terminate_sample_process(process: Any) -> None:
    if not process.is_alive():
        process.join()
        return
    process.terminate()
    process.join(_SAMPLE_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_SAMPLE_SHUTDOWN_SECONDS)


def run_isolated_sample(
    request: SampleRequest,
    *,
    entrypoint: SampleEntrypoint | None = None,
) -> dict[str, object]:
    """Run one sample in a clean spawned process and return only its JSON evidence."""

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=entrypoint or _sample_worker_entry,
        args=(child, request),
        name=f"oaei-o3-{request.reasoner}-{request.backend}",
    )
    try:
        process.start()
        child.close()
        try:
            payload = parent.recv_bytes(_MAX_SAMPLE_RESPONSE_BYTES)
        except (EOFError, OSError) as error:
            _terminate_sample_process(process)
            raise RuntimeError("isolated benchmark sample exited without valid evidence") from error
        process.join(_SAMPLE_SHUTDOWN_SECONDS)
        if process.is_alive():
            _terminate_sample_process(process)
            raise RuntimeError("isolated benchmark sample did not exit after returning evidence")
        if process.exitcode != 0:
            raise RuntimeError(f"isolated benchmark sample exited with status {process.exitcode}")
        try:
            response = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("isolated benchmark sample returned malformed JSON") from error
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise RuntimeError("isolated benchmark sample returned an invalid response envelope")
        if response["ok"] is not True:
            raise RuntimeError(f"isolated benchmark sample failed: {response.get('error')}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("isolated benchmark sample returned an invalid result")
        return result
    finally:
        parent.close()
        child.close()
        if process.is_alive():
            _terminate_sample_process(process)


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"benchmark sample has invalid {label}")
    return value


def _nonnegative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"benchmark sample has invalid {label}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise RuntimeError(f"benchmark sample has invalid {label}")
    return number


def _pipeline_wall_seconds(sample: Mapping[str, object]) -> float:
    seconds = _require_mapping(sample.get("seconds"), "seconds")
    return sum(
        _nonnegative_number(seconds.get(phase), f"seconds.{phase}")
        for phase in _PIPELINE_WALL_PHASES
    )


def _sample_identity(sample: Mapping[str, object]) -> dict[str, object]:
    inputs = _require_mapping(sample.get("inputs"), "inputs")
    results = _require_mapping(sample.get("results"), "results")
    return {
        "python": sample.get("python"),
        "core": sample.get("core"),
        "reasoner": sample.get("reasoner"),
        "class_count_per_side": sample.get("class_count_per_side"),
        "bridge_count": sample.get("bridge_count"),
        "denominator_count": sample.get("denominator_count"),
        "unsatisfiable_count": sample.get("unsatisfiable_count"),
        "inconsistent": sample.get("inconsistent"),
        "inputs": {
            name: inputs.get(name) for name in ("source_sha256", "target_sha256", "bridge_sha256")
        },
        "results": {
            name: results.get(name)
            for name in (
                "denominator_sha256",
                "unsatisfiable_sha256",
                "composite_structural_fingerprint",
            )
        },
    }


def _sample_ingestion_path(sample: Mapping[str, object]) -> object:
    handoff = _require_mapping(sample.get("compiler_handoff"), "compiler_handoff")
    return handoff.get("ingestion_path")


def _sample_counter_evidence(sample: Mapping[str, object]) -> Mapping[str, object]:
    return _require_mapping(sample.get("materialization_and_copy"), "materialization_and_copy")


def _recomputed_counter_evidence(sample: Mapping[str, object]) -> dict[str, object]:
    handoff = _require_mapping(sample.get("compiler_handoff"), "compiler_handoff")
    return _counter_evidence(handoff)


def _cohort_summary(samples: list[dict[str, object]]) -> dict[str, object]:
    wall_samples = [_pipeline_wall_seconds(sample) for sample in samples]
    rss_samples = [
        int(_nonnegative_number(sample.get("process_peak_rss_bytes"), "process_peak_rss_bytes"))
        for sample in samples
    ]
    return {
        "pipeline_wall_seconds": wall_samples,
        "process_peak_rss_bytes": rss_samples,
        "median_pipeline_wall_seconds": float(statistics.median(wall_samples)),
        "median_process_peak_rss_bytes": float(statistics.median(rss_samples)),
        "ingestion_paths": [_sample_ingestion_path(sample) for sample in samples],
        "samples": samples,
    }


def _ratio_evaluation(
    baseline: float,
    candidate: float,
    *,
    regression_limit: float,
) -> dict[str, object]:
    if baseline <= 0:
        return {
            "baseline": baseline,
            "candidate": candidate,
            "candidate_to_baseline_ratio": None,
            "maximum_ratio": 1.0 + regression_limit,
            "passed": False,
        }
    ratio = candidate / baseline
    return {
        "baseline": baseline,
        "candidate": candidate,
        "candidate_to_baseline_ratio": ratio,
        "regression_fraction": ratio - 1.0,
        "maximum_ratio": 1.0 + regression_limit,
        "passed": ratio <= 1.0 + regression_limit,
    }


def evaluate_acceptance_samples(
    baseline_samples: list[dict[str, object]],
    candidate_samples: list[dict[str, object]],
    *,
    warmups: int,
    repetitions: int,
    baseline_backend: str,
    candidate_backend: str,
) -> dict[str, object]:
    """Build one fail-closed scalar-versus-encoded acceptance comparison."""

    if len(baseline_samples) != repetitions or len(candidate_samples) != repetitions:
        raise ValueError("retained sample counts must equal repetitions")
    if not baseline_samples or not candidate_samples:
        raise ValueError("at least one retained baseline and candidate sample is required")

    baseline = _cohort_summary(baseline_samples)
    candidate = _cohort_summary(candidate_samples)
    reference_identity = _sample_identity(baseline_samples[0])
    identity_mismatches = []
    for cohort_name, samples in (
        ("baseline", baseline_samples),
        ("candidate", candidate_samples),
    ):
        for index, sample in enumerate(samples):
            actual = _sample_identity(sample)
            if actual != reference_identity:
                identity_mismatches.append(
                    {
                        "cohort": cohort_name,
                        "sample": index,
                        "expected": reference_identity,
                        "actual": actual,
                    }
                )

    baseline_paths = baseline["ingestion_paths"]
    candidate_paths = candidate["ingestion_paths"]
    assert isinstance(baseline_paths, list)
    assert isinstance(candidate_paths, list)
    baseline_is_scalar = all(path in _SCALAR_INGESTION_PATHS for path in baseline_paths)
    candidate_is_encoded = all(path == "encoded-native" for path in candidate_paths)
    recorded_counter_evidence = [_sample_counter_evidence(sample) for sample in candidate_samples]
    candidate_counter_evidence = [
        _recomputed_counter_evidence(sample) for sample in candidate_samples
    ]
    complete_counter_coverage = all(
        evidence.get("complete_public_counter_coverage") is True
        for evidence in candidate_counter_evidence
    ) and all(
        evidence.get("complete_public_counter_coverage") is True
        for evidence in recorded_counter_evidence
    )
    zero_forbidden_counters = all(
        evidence.get("zero_forbidden_public_counters") is True
        for evidence in candidate_counter_evidence
    ) and all(
        evidence.get("zero_forbidden_public_counters") is True
        and evidence.get("benchmark_wire_encode_calls") == 0
        for evidence in recorded_counter_evidence
    )
    sampling_sufficient = (
        warmups >= MIN_ACCEPTANCE_WARMUPS and repetitions >= MIN_ACCEPTANCE_REPETITIONS
    )
    wall = _ratio_evaluation(
        _nonnegative_number(
            baseline["median_pipeline_wall_seconds"],
            "baseline.median_pipeline_wall_seconds",
        ),
        _nonnegative_number(
            candidate["median_pipeline_wall_seconds"],
            "candidate.median_pipeline_wall_seconds",
        ),
        regression_limit=WALL_REGRESSION_LIMIT,
    )
    rss = _ratio_evaluation(
        _nonnegative_number(
            baseline["median_process_peak_rss_bytes"],
            "baseline.median_process_peak_rss_bytes",
        ),
        _nonnegative_number(
            candidate["median_process_peak_rss_bytes"],
            "candidate.median_process_peak_rss_bytes",
        ),
        regression_limit=RSS_REGRESSION_LIMIT,
    )
    checks = {
        "sampling_sufficient": sampling_sufficient,
        "fixed_input_and_semantic_identity": not identity_mismatches,
        "baseline_selected_scalar_ingestion": baseline_is_scalar,
        "candidate_selected_encoded_native": candidate_is_encoded,
        "candidate_complete_public_counter_coverage": complete_counter_coverage,
        "candidate_zero_forbidden_public_counters": zero_forbidden_counters,
        "wall_regression_within_25_percent": wall["passed"] is True,
        "rss_regression_within_20_percent": rss["passed"] is True,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    protocol_passed = not failed_checks
    return {
        "schema": ACCEPTANCE_SCHEMA,
        "protocol": {
            "sample_isolation": "fresh-spawned-process",
            "ordering": "alternating-scalar-and-candidate",
            "statistic": "median-of-retained-samples",
            "warmups_per_backend": warmups,
            "repetitions_per_backend": repetitions,
            "minimum_warmups": MIN_ACCEPTANCE_WARMUPS,
            "minimum_repetitions": MIN_ACCEPTANCE_REPETITIONS,
            "scalar_baseline_backend": baseline_backend,
            "candidate_backend": candidate_backend,
            "maximum_wall_regression_fraction": WALL_REGRESSION_LIMIT,
            "maximum_rss_regression_fraction": RSS_REGRESSION_LIMIT,
        },
        "workload": {
            "kind": "generated-el-fixture",
            "class_count_per_side": reference_identity["class_count_per_side"],
            "bridge_count": reference_identity["bridge_count"],
            "diagnostic_only": True,
            "release_gate_eligible": False,
            "reason": (
                "generated fixtures exercise the protocol but do not replace pinned "
                "Conference/Bio-ML, GO/NCIT, or licensed SNOMED-scale evidence"
            ),
        },
        "identity": {
            "reference": reference_identity,
            "fixed": not identity_mismatches,
            "mismatches": identity_mismatches,
        },
        "baseline": baseline,
        "candidate": candidate,
        "evaluation": {
            "checks": checks,
            "wall": wall,
            "rss": rss,
            "failed_checks": failed_checks,
            "protocol_passed": protocol_passed,
            "release_accepted": False,
        },
    }


def run_acceptance_protocol(
    class_count: int,
    bridge_count: int,
    *,
    reasoner: str,
    baseline_backend: str,
    candidate_backend: str,
    timeout_s: float | None,
    warmups: int,
    repetitions: int,
    sample_runner: SampleRunner | None = None,
) -> dict[str, object]:
    """Collect alternating isolated samples and evaluate the frozen regression gates."""

    for label, value in (("warmups", warmups), ("repetitions", repetitions)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < (0 if label == "warmups" else 1)
        ):
            raise ValueError(
                f"{label} must be a nonnegative integer"
                if label == "warmups"
                else f"{label} must be a positive integer"
            )
    runner = sample_runner or run_isolated_sample
    requests = {
        "baseline": SampleRequest(
            class_count,
            bridge_count,
            reasoner,
            baseline_backend,
            timeout_s,
        ),
        "candidate": SampleRequest(
            class_count,
            bridge_count,
            reasoner,
            candidate_backend,
            timeout_s,
        ),
    }
    baseline_samples: list[dict[str, object]] = []
    candidate_samples: list[dict[str, object]] = []
    retained = {"baseline": baseline_samples, "candidate": candidate_samples}
    for round_index in range(warmups + repetitions):
        order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
        for cohort in order:
            sample = runner(requests[cohort])
            if round_index >= warmups:
                retained[cohort].append(sample)
    return evaluate_acceptance_samples(
        baseline_samples,
        candidate_samples,
        warmups=warmups,
        repetitions=repetitions,
        baseline_backend=baseline_backend,
        candidate_backend=candidate_backend,
    )


def _timeout(value: str) -> float | None:
    if value.lower() in {"none", "unbounded"}:
        return None
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive or 'none'")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reasoner", choices=("hermit", "elk"), required=True)
    parser.add_argument(
        "--backend", default="auto", help="candidate backend (or single-run backend)"
    )
    parser.add_argument(
        "--baseline-backend",
        default=None,
        help="enable acceptance comparison against this scalar backend (normally 'python')",
    )
    parser.add_argument("--class-count", type=int, default=1000)
    parser.add_argument("--bridge-count", type=int, default=500)
    parser.add_argument("--timeout", type=_timeout, default=None)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "exit nonzero unless the candidate is encoded-native with complete zero-forbidden "
            "counter evidence, exact identity, and accepted wall/RSS ratios"
        ),
    )
    args = parser.parse_args(argv)
    if args.class_count < 1:
        parser.error("--class-count must be positive")
    if not 0 <= args.bridge_count <= args.class_count:
        parser.error("--bridge-count must lie between zero and --class-count")
    if args.reasoner == "elk" and args.timeout is not None:
        parser.error("--timeout is only supported by the HermiT benchmark")
    if args.warmups < 0:
        parser.error("--warmups must be nonnegative")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if args.enforce and args.baseline_backend is None:
        parser.error("--enforce requires --baseline-backend")

    if args.baseline_backend is None:
        payload = run(
            args.class_count,
            args.bridge_count,
            reasoner=args.reasoner,
            backend=args.backend,
            timeout_s=args.timeout,
        )
    else:
        payload = run_acceptance_protocol(
            args.class_count,
            args.bridge_count,
            reasoner=args.reasoner,
            baseline_backend=args.baseline_backend,
            candidate_backend=args.backend,
            timeout_s=args.timeout,
            warmups=args.warmups,
            repetitions=args.repetitions,
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.enforce:
        evaluation = _require_mapping(payload.get("evaluation"), "evaluation")
        if evaluation.get("protocol_passed") is not True:
            failed = evaluation.get("failed_checks")
            print(f"encoded performance acceptance failed: {failed}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
