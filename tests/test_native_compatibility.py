"""Machine-readable final-core and advertised reasoner contract binding."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from oaei_bioml_eval.coherence import native_reasoners

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "release" / "core-compatibility.json"


class TestNativeCompatibilityContract(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_exact_production_releases_and_historical_attestations_are_bound(self) -> None:
        self.assertEqual(
            self.contract["tested_core"],
            {
                "repository": "https://github.com/OAEI-ML/pyOWLCore",
                "commit": "d3e7893b0609fcd7df390375267a00356f09cb22",
                "tree": "32cc4cbf9c99f1b45785cb29f4f059ec0f86a691",
                "version": "0.1.0",
            },
        )
        self.assertEqual(
            self.contract["historical_tested_core"],
            {
                "repository": "https://github.com/OAEI-ML/pyOWLCore",
                "commit": "005c3ccad129757b3a9be125dc064b812b607ef5",
                "tree": "d4f3f29f6594b59f3d45a4811c38fb761a7028b9",
                "version": "0.1.0.dev0",
            },
        )
        reasoners = self.contract["reasoners"]
        self.assertEqual(
            reasoners["pyelk"],
            {
                "repository": "https://github.com/OAEI-ML/pyELK",
                "production_release_commit": (
                    "487de1cf47ba72b6adb85cd809e0358b28c391dc"
                ),
                "production_release_tree": (
                    "39940247e95ccc6e3ec8353a670541d6ff8b8ec3"
                ),
                "production_release_version": "0.1.0",
                "final_attestation_commit": (
                    "70302fcd6abc27d703eeb8f59027fc1392f4709b"
                ),
                "release_contract_commit": (
                    "faf7a995bd4b44964d7e5a56007ae484df79d597"
                ),
                "runtime_contract_commit": (
                    "bc75f4be609626f231cdc91af800f52bae46c766"
                ),
                "parity_contract": "wp14-encoded-public-dispatch-short",
            },
        )
        self.assertEqual(
            reasoners["pyhermit"],
            {
                "repository": "https://github.com/OAEI-ML/pyHermiT",
                "production_release_commit": (
                    "1b153139f97820f675f90bea8182e38c51c3d5e2"
                ),
                "production_release_tree": (
                    "c269f52667092dd76bf43d61fef388031e774482"
                ),
                "production_release_version": "0.1.1",
                "historical_production_release": {
                    "commit": "2bbc4d6e2d01747367b9f3d497f14c9da1012bb1",
                    "tree": "1c5dfb414704880942298359a4424dca55fc588c",
                    "version": "0.1.0",
                },
                "final_attestation_commit": (
                    "af8f7fc669b28dfc15728c84c78f9094787d288b"
                ),
                "attestation_source_commit": (
                    "3dd752b1ccff643dc874bbb47cf9d2eb08b6ae3c"
                ),
                "runtime_contract_commit": (
                    "f0d4ebb270f3521b848cd2a858761afd66e72ae2"
                ),
                "parity_contract": "wp18-encoded-public-dispatch-short",
            },
        )

    def test_runtime_fences_equal_the_machine_readable_contract(self) -> None:
        encoded = self.contract["encoded_ingestion"]
        self.assertEqual(encoded["schema_name"], native_reasoners._ENCODED_SCHEMA_NAME)
        self.assertEqual(encoded["schema_version"], native_reasoners._ENCODED_SCHEMA_VERSION)
        self.assertEqual(encoded["model_schema"], native_reasoners._ENCODED_MODEL_SCHEMA)
        self.assertEqual(
            encoded["descriptor_sha256"],
            native_reasoners._ENCODED_DESCRIPTOR_SHA256,
        )
        self.assertEqual(
            encoded["required_core_fields"],
            sorted(native_reasoners._BACKEND_CORE_CONTRACT_FIELDS),
        )
        self.assertEqual(
            encoded["required_counters"],
            sorted(native_reasoners._ENCODED_REQUIRED_COUNTERS),
        )
        self.assertEqual(
            encoded["required_zero_counters"],
            sorted(native_reasoners._ENCODED_REQUIRED_ZERO_COUNTERS),
        )

    def test_dependency_ranges_and_sdist_evidence_fail_closed_together(self) -> None:
        constraints = set(self.contract["dependency_constraints"].values())
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for constraint in constraints:
            self.assertEqual(metadata.count(f'"{constraint}"'), 2)
        self.assertIn('  "/release",', metadata)

    def test_owner_and_transport_scope_is_exact(self) -> None:
        self.assertEqual(
            self.contract["schema"],
            "oaei-bioml-eval.core-compatibility/2",
        )
        runtime = self.contract["oaei_runtime"]
        self.assertEqual(
            runtime["handoff_baseline_commit"],
            "fd75aedbf9f5ed4351d3f6d634a6e07721d21778",
        )
        self.assertEqual(
            runtime["owner_matrix"],
            ["direct", "mmap", "overlay", "composite"],
        )
        self.assertEqual(runtime["in_process_transport"], "identity")
        self.assertEqual(runtime["bounded_fallback_transport"], "core-wire-worker")


if __name__ == "__main__":
    unittest.main()
