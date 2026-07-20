#!/usr/bin/env python3
"""Measure the complete Java-free O3 shared-view reasoner pipeline."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import resource
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from typing import TypeVar

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
        declarations.append(
            f"Declaration(Class(<{source_iri if source else target_iri}>))"
        )
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
    public_counters = compiler_handoff.get("counters")
    counters = dict(public_counters) if isinstance(public_counters, Mapping) else {}
    ingestion_path = compiler_handoff.get("ingestion_path")
    materialized_rows = counters.get("materialized_scalar_rows")
    staging_copy_bytes = counters.get("encoded_staging_copy_bytes")
    required_counter_names = (
        "base_flattening_bytes",
        "parser_calls",
        "per_row_ffi_calls",
        "resolver_calls",
        "wire_decoder_calls",
        "wire_encoder_calls",
    )
    missing_public_counters = [name for name in required_counter_names if name not in counters]
    complete_counter_coverage = (
        ingestion_path == "encoded-native"
        and not missing_public_counters
        and ("materialized_scalar_rows" in counters or "scalar_axiom_materializations" in counters)
        and ("encoded_staging_copy_bytes" in counters or "structural_copy_bytes" in counters)
    )
    return {
        "schema": "oaei-bioml-eval.o3-native-benchmark/2",
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
        "materialization_and_copy": {
            "public_counters": counters,
            "materialized_scalar_rows": materialized_rows,
            "encoded_staging_copy_bytes": staging_copy_bytes,
            "copied_structural_bytes": counters.get("structural_copy_bytes"),
            "complete_public_counter_coverage": complete_counter_coverage,
            "missing_public_counters": missing_public_counters,
            "benchmark_wire_encode_calls": 0,
            "selected_ingestion_path": ingestion_path,
        },
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
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--class-count", type=int, default=1000)
    parser.add_argument("--bridge-count", type=int, default=500)
    parser.add_argument("--timeout", type=_timeout, default=None)
    args = parser.parse_args(argv)
    if args.class_count < 1:
        parser.error("--class-count must be positive")
    if not 0 <= args.bridge_count <= args.class_count:
        parser.error("--bridge-count must lie between zero and --class-count")
    if args.reasoner == "elk" and args.timeout is not None:
        parser.error("--timeout is only supported by the HermiT benchmark")
    print(
        json.dumps(
            run(
                args.class_count,
                args.bridge_count,
                reasoner=args.reasoner,
                backend=args.backend,
                timeout_s=args.timeout,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
