"""Integrity checks for committed, non-runtime release evidence."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "O3-ncit-doid-schema2.json"
BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10-ncit-doid-train.json"
REPORT_SHA256 = "23ee9f77cc8fb24e4b2652173f5e4b87e25b36aa4d867810113d283459e917b7"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError(f"expected an object in {path}")
    return value


class TestReleaseEvidence(unittest.TestCase):
    def test_ncit_schema2_capture_binds_oracle_and_native_runtime(self) -> None:
        report_bytes = REPORT.read_bytes()
        baseline_bytes = BASELINE.read_bytes()
        report = _load(REPORT)
        baseline = _load(BASELINE)

        self.assertEqual(hashlib.sha256(report_bytes).hexdigest(), REPORT_SHA256)
        self.assertEqual(report["schema"], "oaei-bioml-eval.native-comparison/2")
        self.assertIs(report["agreement"], True)
        self.assertEqual(report["baseline_schema"], baseline["schema"])
        self.assertEqual(report["baseline"]["bytes"], len(baseline_bytes))
        self.assertEqual(
            report["baseline"]["sha256"],
            hashlib.sha256(baseline_bytes).hexdigest(),
        )
        self.assertEqual(
            report["counts"],
            {
                "correspondences": baseline["bridge"]["mapping_count"],
                "named_classes": baseline["inputs"]["named_class_count"],
                "unsatisfiable": baseline["comparison"]["unsatisfiable_count"],
            },
        )
        self.assertEqual(
            report["unsatisfiable_sha256"],
            baseline["comparison"]["unsatisfiable_sha256"],
        )
        self.assertEqual(
            report["inputs"]["alignment"]["sha256"], baseline["bridge"]["sha256"]
        )
        self.assertEqual(
            report["inputs"]["source"]["sha256"],
            baseline["inputs"]["source"]["sha256"],
        )
        self.assertEqual(
            report["inputs"]["target"]["sha256"],
            baseline["inputs"]["target"]["sha256"],
        )
        self.assertIs(report["runtime"]["accelerated"], True)
        self.assertEqual(report["runtime"]["backend"]["name"], "rust")
        self.assertEqual(
            report["runtime"]["native_artifact"]["sha256"],
            "5774b58598dbb7fbbd618b0bfc9c848983421292a6627ed7f5b423b7340b2990",
        )


if __name__ == "__main__":
    unittest.main()
