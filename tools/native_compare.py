#!/usr/bin/env python3
"""Compare native coherence with the pinned public NCIT-DOID migration evidence.

This is a Java-free manual release gate. The public ontology/alignment files stay
outside Git; their pinned hashes prevent an accidental comparison with different
data. Until pyHermiT, pyELK, and core wire expose their frozen facades, the command
fails at the explicit adapter capability boundary and produces no claimed result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.coherence.report import score_reference_coherence_files  # noqa: E402

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10-ncit-doid-train.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_report(
    report: dict[str, Any], baseline: dict[str, Any], *, reasoner: str
) -> dict[str, Any]:
    """Fail on any frozen semantic count/digest mismatch."""

    run = baseline["runs"][reasoner]
    expected = {
        "union_class_count": baseline["inputs"]["named_class_count"],
        "asserted_correspondences": baseline["bridge"]["mapping_count"],
        "unsatisfiable_count": run["unsatisfiable_count"],
        "numerator_sha256": run["unsatisfiable_sha256"],
    }
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("schema") != "coherence-provenance/1":
        raise RuntimeError("native report lacks coherence-provenance/1")
    result = provenance.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("native report lacks provenance result digests")
    actual = {
        "reasoner_used": report.get("reasoner_used"),
        "union_class_count": report.get("union_class_count"),
        "asserted_correspondences": report.get("asserted_correspondences"),
        "unsatisfiable_count": report.get("unsatisfiable_count"),
        "numerator_sha256": result.get("numerator_sha256"),
    }
    mismatches = {
        key: {"actual": actual[key], "expected": expected[key]}
        for key in expected
        if actual[key] != expected[key]
    }
    if actual["reasoner_used"] != reasoner:
        mismatches["reasoner_used"] = {
            "actual": actual["reasoner_used"],
            "expected": reasoner,
        }
    if mismatches:
        raise RuntimeError(
            "native result differs from the frozen NCIT-DOID evidence: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return {
        "schema": "oaei-bioml-eval.native-comparison/1",
        "baseline_schema": baseline["schema"],
        "reasoner": reasoner,
        "agreement": True,
        "counts": {
            "named_classes": actual["union_class_count"],
            "correspondences": actual["asserted_correspondences"],
            "unsatisfiable": actual["unsatisfiable_count"],
        },
        "unsatisfiable_sha256": actual["numerator_sha256"],
        "provenance": provenance,
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
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--alignment", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--reasoner", choices=("hermit", "elk"), required=True)
    parser.add_argument("--timeout", type=_timeout, default=7200.0)
    args = parser.parse_args(argv)

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    pinned_hashes = {
        "source": baseline["inputs"]["source"]["sha256"],
        "target": baseline["inputs"]["target"]["sha256"],
        "alignment": baseline["bridge"]["sha256"],
    }
    actual_hashes = {
        "source": sha256_file(args.source),
        "target": sha256_file(args.target),
        "alignment": sha256_file(args.alignment),
    }
    if actual_hashes != pinned_hashes:
        raise SystemExit(
            "input hashes do not match the pinned public NCIT-DOID evidence: "
            + json.dumps(
                {"actual": actual_hashes, "expected": pinned_hashes}, sort_keys=True
            )
        )
    report = score_reference_coherence_files(
        args.alignment,
        args.source,
        args.target,
        reasoner=args.reasoner,
        timeout_s=args.timeout,
        backend="native",
        source_document_iri="urn:oaei:ncit",
        target_document_iri="urn:oaei:doid",
    )
    print(json.dumps(compare_report(report, baseline, reasoner=args.reasoner), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
