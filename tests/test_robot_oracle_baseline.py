"""Java-free integrity checks for the committed ROBOT oracle output."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
LARGE_EVIDENCE = (
    ROOT / "tests" / "baselines" / "robot-1.9.10-ncit-doid-train.json"
)
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"


class TestRobotOracleBaseline(unittest.TestCase):
    payload: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_provenance_is_pinned(self) -> None:
        self.assertEqual(self.payload["schema"], "oaei-bioml-eval.robot-oracle/1")
        self.assertEqual(self.payload["robot"]["version"], "ROBOT version 1.9.10")
        self.assertEqual(len(self.payload["robot"]["sha256"]), 64)

    def test_fixture_hashes_are_current(self) -> None:
        for case in self.payload["cases"].values():
            for side in ("source", "target"):
                path = FIXTURES / case[side]
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, case[f"{side}_sha256"])

    def test_frozen_semantics_cover_clean_equivalence_and_subsumption(self) -> None:
        cases = self.payload["cases"]
        self.assertEqual(cases["equivalence_clean"]["hermit_unsatisfiable"], [])
        self.assertEqual(
            cases["equivalence_clash"]["hermit_unsatisfiable"],
            ["http://ex.org/A", "http://ex.org/B"],
        )
        self.assertEqual(
            cases["subsumption_forward_clash"]["hermit_unsatisfiable"],
            ["http://ex.org/A"],
        )
        self.assertEqual(
            cases["subsumption_reverse_clash"]["hermit_unsatisfiable"],
            ["http://ex.org/B"],
        )
        self.assertEqual(
            cases["already_incoherent"]["hermit_unsatisfiable"],
            ["http://ex.org/A"],
        )

    def test_large_ncit_doid_evidence_records_exact_reasoner_agreement(self) -> None:
        evidence = json.loads(LARGE_EVIDENCE.read_text(encoding="utf-8"))
        comparison = evidence["comparison"]
        hermit = evidence["runs"]["hermit"]
        elk = evidence["runs"]["elk"]

        self.assertEqual(
            evidence["schema"], "oaei-bioml-eval.robot-large-evidence/1"
        )
        self.assertEqual(evidence["bridge"]["mapping_count"], 1406)
        self.assertEqual(evidence["inputs"]["named_class_count"], 24227)
        self.assertTrue(comparison["agreement"])
        self.assertEqual(comparison["unsatisfiable_count"], 2227)
        self.assertEqual(hermit["unsatisfiable_count"], elk["unsatisfiable_count"])
        self.assertEqual(
            hermit["unsatisfiable_sha256"], elk["unsatisfiable_sha256"]
        )
        self.assertEqual(
            comparison["unsatisfiable_sha256"], hermit["unsatisfiable_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
