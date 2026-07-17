#!/usr/bin/env python3
"""Run native reasoners against every pinned small ROBOT migration fixture.

The committed oracle JSON is read-only evidence. This Java-free gate recomputes
metrics through the shared-view API and requires exact denominator, numerator
count, and sorted-IRI digest agreement for both native reasoners.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.coherence.provenance import sorted_line_sha256  # noqa: E402
from oaei_bioml_eval.coherence.report import score_reference_coherence  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"


def compare_case(
    report: dict[str, Any], expected: dict[str, Any], *, reasoner: str
) -> None:
    provenance = report.get("provenance")
    result = provenance.get("result") if isinstance(provenance, dict) else None
    expected_unsatisfiable = expected[f"{reasoner}_unsatisfiable"]
    wanted = {
        "reasoner_used": reasoner,
        "union_class_count": len(expected["named_classes"]),
        "unsatisfiable_count": len(expected_unsatisfiable),
        "numerator_sha256": sorted_line_sha256(expected_unsatisfiable),
    }
    actual = {
        "reasoner_used": report.get("reasoner_used"),
        "union_class_count": report.get("union_class_count"),
        "unsatisfiable_count": report.get("unsatisfiable_count"),
        "numerator_sha256": (
            result.get("numerator_sha256") if isinstance(result, dict) else None
        ),
    }
    if actual != wanted:
        raise RuntimeError(
            "native fixture mismatch: "
            + json.dumps({"actual": actual, "expected": wanted}, sort_keys=True)
        )


def capture(*, reasoner: str, timeout_s: float) -> dict[str, Any]:
    if os.environ.get("OAEI_COHERENCE_BACKEND") not in {None, "", "native"}:
        raise RuntimeError(
            "unset OAEI_COHERENCE_BACKEND: fixture comparison permits only native adapters"
        )
    import pyowl_core

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    output: dict[str, Any] = {
        "schema": "oaei-bioml-eval.native-fixture-comparison/1",
        "baseline_schema": baseline["schema"],
        "reasoner": reasoner,
        "cases": {},
    }
    for case_id, expected in sorted(baseline["cases"].items()):
        source = pyowl_core.coerce_snapshot(
            FIXTURES / expected["source"],
            document_iri=f"urn:oaei:fixture:{case_id}:source",
        )
        target = pyowl_core.coerce_snapshot(
            FIXTURES / expected["target"],
            document_iri=f"urn:oaei:fixture:{case_id}:target",
        )
        report = score_reference_coherence(
            [tuple(item) for item in expected["bridge"]],
            source,
            target,
            reasoner=reasoner,
            timeout_s=timeout_s,
        )
        compare_case(report, expected, reasoner=reasoner)
        output["cases"][case_id] = {
            "agreement": True,
            "unsatisfiable_count": report["unsatisfiable_count"],
            "union_class_count": report["union_class_count"],
            "provenance": report["provenance"],
        }
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reasoner", choices=("hermit", "elk"), required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    print(json.dumps(capture(reasoner=args.reasoner, timeout_s=args.timeout), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
