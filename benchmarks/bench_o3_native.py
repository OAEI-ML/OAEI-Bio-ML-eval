#!/usr/bin/env python3
"""Measure the complete Java-free O3 shared-view reasoner pipeline."""

from __future__ import annotations

import argparse
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
from oaei_bioml_eval.coherence.provenance import (
    build_coherence_provenance,
    canonical_provenance_json,
)
from oaei_bioml_eval.coherence.reasoner import UnsatResult

T = TypeVar("T")
OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _measure(function: Callable[[], T]) -> tuple[T, float, int]:
    rss_before = _peak_rss_bytes()
    started = time.perf_counter()
    value = function()
    elapsed = time.perf_counter() - started
    return value, elapsed, max(0, _peak_rss_bytes() - rss_before)


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


def _backend_record(backend: object) -> dict[str, object]:
    record: dict[str, object] = {}
    for name in (
        "name",
        "implementation_version",
        "ir_schema_version",
        "ir_major",
        "ir_minor",
        "accelerated",
        "native_available",
        "requested_workers",
        "effective_workers",
        "fallback_reason",
    ):
        value = getattr(backend, name, None)
        if value is None or isinstance(value, (str, int, float, bool)):
            record[name] = value
    return record


def _reason(
    ontology: object,
    denominator: tuple[str, ...],
    *,
    reasoner: str,
    backend: str,
    timeout_s: float | None,
) -> tuple[tuple[str, ...], bool, Mapping[str, object], dict[str, float], dict[str, int]]:
    timings: dict[str, float] = {}
    rss: dict[str, int] = {}
    if reasoner == "elk":
        module = importlib.import_module("pyelk")
        if backend not in {"auto", "python", "rust"}:
            raise ValueError("pyELK backend must be auto, python, or rust")
        config = module.ReasonerConfig(backend=backend)
        session, timings["reasoner_compile"], rss["reasoner_compile"] = _measure(
            lambda: module.Reasoner(ontology, config)
        )
        try:
            consistency, timings["consistency"], rss["consistency"] = _measure(
                session.is_consistent
            )
            consistent = consistency.value
            taxonomy, timings["classification"], rss["classification"] = _measure(
                session.classify
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
            metadata = {
                "schema": "native-reasoner-provenance/1",
                "package": "pyELK",
                "package_version": importlib.metadata.version("pyelk-reasoner"),
                "backend": _backend_record(session.backend),
                "transport": {"mode": "in-process-identity"},
                "profile": profile,
            }
            return unsatisfiable, not consistent, metadata, timings, rss
        finally:
            session.close()

    module = importlib.import_module("pyhermit")
    if backend not in {"auto", "python", "native", "verify"}:
        raise ValueError("pyHermiT backend must be auto, python, native, or verify")
    config = module.ReasonerConfig(backend=backend, timeout=timeout_s)
    session, timings["reasoner_compile"], rss["reasoner_compile"] = _measure(
        lambda: module.Reasoner(ontology, config=config)
    )
    try:
        consistent, timings["consistency"], rss["consistency"] = _measure(
            session.is_consistent
        )
        if consistent:
            values, timings["classification"], rss["classification"] = _measure(
                session.unsatisfiable_classes
            )
            unsatisfiable = _entity_iris(values)
        else:
            timings["classification"] = 0.0
            rss["classification"] = 0
            unsatisfiable = denominator
        metadata = {
            "schema": "native-reasoner-provenance/1",
            "package": "pyHermiT",
            "package_version": importlib.metadata.version("pyHermiT"),
            "backend": _backend_record(session.backend),
            "transport": {"mode": "in-process-identity"},
            "profile": {"complete": True, "reasons": []},
        }
        return unsatisfiable, not consistent, metadata, timings, rss
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
    timings: dict[str, float] = {}
    rss: dict[str, int] = {}
    source, timings["source_load"], rss["source_load"] = _measure(
        lambda: pyowl_core.coerce_snapshot(
            _ontology_bytes(class_count, source=True),
            document_iri="urn:oaei:benchmark:source-document",
        )
    )
    target, timings["target_load"], rss["target_load"] = _measure(
        lambda: pyowl_core.coerce_snapshot(
            _ontology_bytes(class_count, source=False),
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
    bridge, timings["bridge_normalization"], rss["bridge_normalization"] = _measure(
        lambda: analyze_correspondences(pairs)
    )
    composite, timings["composition"], rss["composition"] = _measure(
        lambda: compose_alignment_views(source, target, bridge.correspondences)
    )
    denominator, timings["signature"], rss["signature"] = _measure(
        lambda: named_class_iris(composite)
    )
    wire, timings["wire_encode"], rss["wire_encode"] = _measure(
        lambda: pyowl_core.encode_snapshot(composite)
    )
    reasoned, timings["reasoning_total"], rss["reasoning_total"] = _measure(
        lambda: _reason(
            composite,
            denominator,
            reasoner=reasoner,
            backend=backend,
            timeout_s=timeout_s,
        )
    )
    unsatisfiable, inconsistent, reasoner_metadata, split_timings, split_rss = reasoned
    timings.update(split_timings)
    rss.update(split_rss)
    result = UnsatResult(
        unsatisfiable,
        reasoner,
        sum(split_timings.values()),
        provenance=reasoner_metadata,
        inconsistent=inconsistent,
    )
    provenance, timings["reporting"], rss["reporting"] = _measure(
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
    return {
        "schema": "oaei-bioml-eval.o3-native-benchmark/1",
        "python": sys.version.split()[0],
        "core": pyowl_core.__version__,
        "reasoner": reasoner,
        "backend": reasoner_metadata["backend"],
        "class_count_per_side": class_count,
        "bridge_count": bridge_count,
        "denominator_count": len(denominator),
        "unsatisfiable_count": len(unsatisfiable),
        "inconsistent": inconsistent,
        "wire_bytes": len(wire),
        "provenance_bytes": len(canonical_provenance_json(provenance).encode()),
        "seconds": timings,
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
