#!/usr/bin/env python3
"""Measure O3 composition/signature/provenance overhead without a reasoner double."""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time

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


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def run(count: int) -> dict[str, object]:
    source = pyowl_core.coerce_snapshot(
        b"Ontology(<urn:benchmark:source>)",
        document_iri="urn:benchmark:source-document",
    )
    target = pyowl_core.coerce_snapshot(
        b"Ontology(<urn:benchmark:target>)",
        document_iri="urn:benchmark:target-document",
    )
    pairs = tuple(
        (f"urn:benchmark:source:{index}", f"urn:benchmark:target:{index}")
        for index in range(count)
    )
    started = time.perf_counter()
    bridge = analyze_correspondences(pairs)
    normalized_seconds = time.perf_counter() - started
    started = time.perf_counter()
    composite = compose_alignment_views(source, target, bridge.correspondences)
    composition_seconds = time.perf_counter() - started
    started = time.perf_counter()
    denominator = named_class_iris(composite)
    signature_seconds = time.perf_counter() - started
    started = time.perf_counter()
    provenance = build_coherence_provenance(
        composite,
        bridge,
        denominator,
        UnsatResult((), "hermit", 0.0),
        requested_reasoner="hermit",
        timeout_s=7200.0,
        api_options={"metric": "global", "benchmark": True},
    )
    provenance_cold_seconds = time.perf_counter() - started
    started = time.perf_counter()
    warm_provenance = build_coherence_provenance(
        composite,
        bridge,
        denominator,
        UnsatResult((), "hermit", 0.0),
        requested_reasoner="hermit",
        timeout_s=7200.0,
        api_options={"metric": "global", "benchmark": True},
    )
    provenance_warm_seconds = time.perf_counter() - started
    if provenance != warm_provenance:
        raise RuntimeError("warm provenance changed semantic content")
    return {
        "schema": "oaei-bioml-eval.o3-foundation-benchmark/1",
        "python": sys.version.split()[0],
        "core": pyowl_core.__version__,
        "bridge_count": count,
        "denominator_count": len(denominator),
        "delta_entries": composite.delta.entry_count,
        "source_identity": composite.members[0].view is source,
        "target_identity": composite.members[1].view is target,
        "seconds": {
            "normalization": normalized_seconds,
            "composition": composition_seconds,
            "signature": signature_seconds,
            "provenance_cold": provenance_cold_seconds,
            "provenance_warm": provenance_warm_seconds,
        },
        "provenance_bytes": len(canonical_provenance_json(provenance).encode("utf-8")),
        "process_peak_rss_bytes": _peak_rss_bytes(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-count", type=int, default=5000)
    args = parser.parse_args(argv)
    if args.bridge_count < 0:
        parser.error("--bridge-count must be nonnegative")
    print(json.dumps(run(args.bridge_count), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
