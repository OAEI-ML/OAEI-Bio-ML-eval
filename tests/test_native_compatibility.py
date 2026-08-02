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

    def test_final_stack_and_historical_attestations_are_bound(self) -> None:
        self.assertEqual(self.contract["release_pin_status"], "final")
        self.assertEqual(self.contract["pending_final_pins"], [])
        self.assertEqual(
            self.contract["tested_core"],
            {
                "repository": "https://github.com/OAEI-ML/pyOWLCore",
                "commit": "422a63363e0b67857eebfca6dd67595ebaad7a09",
                "tree": "56dc47d73870a786a1248d89bf10a89155fcffff",
                "version": "0.2.0",
                "api_version": [0, 2],
                "model_schema": 2,
                "wire_format": [1, 2],
                "adapter_protocol": 1,
            },
        )
        self.assertEqual(
            self.contract["historical_tested_core"],
            {
                "repository": "https://github.com/OAEI-ML/pyOWLCore",
                "commit": "b0d8fd27537b2f177cfe9a5e0fd41f33b9f18f19",
                "tree": "e72fc93248cd363a5c67dac9efffb367a71c2b1d",
                "version": "0.1.1",
            },
        )
        self.assertEqual(
            self.contract["legacy_development_core"],
            {
                "repository": "https://github.com/OAEI-ML/pyOWLCore",
                "commit": "005c3ccad129757b3a9be125dc064b812b607ef5",
                "tree": "d4f3f29f6594b59f3d45a4811c38fb761a7028b9",
                "version": "0.1.0.dev0",
            },
        )
        reasoners = self.contract["reasoners"]
        self.assertEqual(
            self.contract["sibling_consumers"],
            {
                "pyowl2vec-star-projector": {
                    "repository": "https://github.com/OAEI-ML/pyOwl2Vec-Star-projector",
                    "production_release_commit": (
                        "e04973c6b794b5a379aa5f3392dfd6573a3336a5"
                    ),
                    "production_release_tree": (
                        "eb48642ceb53dd35ce0417672f5e72953c1e2e22"
                    ),
                    "production_release_version": "0.2.0",
                    "prior_production_release": {
                        "commit": "a0f800c1223dc7e66b0fab8a49417ade7b690568",
                        "tree": "893ef426e452a55d5aa67856621775746755c558",
                        "version": "0.1.1",
                    },
                    "dependency_relationship": "none",
                }
            },
        )
        self.assertNotIn(
            "pyowl2vec-star-projector",
            self.contract["dependency_constraints"],
        )
        self.assertEqual(
            reasoners["pyelk"],
            {
                "repository": "https://github.com/OAEI-ML/pyELK",
                "production_release_commit": ("ac8926f646ba5c4a5aba700cff8f159112880ac8"),
                "production_release_tree": ("4e404fc275cedbbc2f0b54e7f27971edf4826cf1"),
                "production_release_version": "0.2.0",
                "prior_production_release": {
                    "commit": "169361c17f23a50e9bc6837617dd7881dcd28be8",
                    "tree": "bc5072e3aa206a04361f3d492e7d43f7d8542e2a",
                    "version": "0.1.1",
                },
                "historical_production_release": {
                    "commit": "487de1cf47ba72b6adb85cd809e0358b28c391dc",
                    "tree": "39940247e95ccc6e3ec8353a670541d6ff8b8ec3",
                    "version": "0.1.0",
                },
                "historical_final_attestation_commit": ("70302fcd6abc27d703eeb8f59027fc1392f4709b"),
                "historical_release_contract_commit": ("faf7a995bd4b44964d7e5a56007ae484df79d597"),
                "historical_runtime_contract_commit": ("bc75f4be609626f231cdc91af800f52bae46c766"),
                "parity_contract": "wp14-encoded-public-dispatch-short",
            },
        )
        self.assertEqual(
            reasoners["pyhermit"],
            {
                "repository": "https://github.com/OAEI-ML/pyHermiT",
                "production_release_commit": ("e371e15cdbc0e83098887aedba831520ede2a1a7"),
                "production_release_tree": ("45da63645a3cfc86a9c0d859700bd9a1144f0650"),
                "production_release_version": "0.2.0",
                "prior_production_release": {
                    "commit": "742bd71b3e9c2d38ba170561e5b4d26f240dccfd",
                    "tree": "56753deb518099dfae1cb72dd8564c7bb3cca58a",
                    "version": "0.1.2",
                },
                "historical_production_release": {
                    "commit": "777725b3bf054dfc0bd0d3b98cc133c4b0469ca1",
                    "tree": "019fc1c2a4c7002ca8985e06c5b9942b7ba26c12",
                    "version": "0.1.1",
                },
                "historical_final_attestation_commit": ("af8f7fc669b28dfc15728c84c78f9094787d288b"),
                "historical_attestation_source_commit": (
                    "3dd752b1ccff643dc874bbb47cf9d2eb08b6ae3c"
                ),
                "historical_runtime_contract_commit": ("f0d4ebb270f3521b848cd2a858761afd66e72ae2"),
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
            "oaei-bioml-eval.core-compatibility/3",
        )
        runtime = self.contract["oaei_runtime"]
        self.assertEqual(runtime["package_version"], "0.2.1")
        self.assertEqual(
            runtime["implementation_subject"],
            {
                "repository": "https://github.com/OAEI-ML/OAEI-Bio-ML-eval",
                "commit": "b0d061e405147cc8684b3e7fd22ed366bd2ca452",
                "tree": "1c6c0773d5af93c2bf3769eabeb3b228d4f1eb8b",
                "version": "0.2.1",
            },
        )
        self.assertEqual(runtime["required_core_api"], [0, 2])
        self.assertEqual(runtime["required_model_schema"], 2)
        self.assertEqual(runtime["required_wire_format"], [1, 2])
        self.assertEqual(runtime["required_adapter_protocol"], 1)
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
