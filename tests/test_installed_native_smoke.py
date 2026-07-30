"""Executable contract tests for the installed native owner matrix."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import unittest

_HAS_CORE = importlib.util.find_spec("pyowl_core") is not None


def _installed(module: str, distribution: str) -> bool:
    if importlib.util.find_spec(module) is None:
        return False
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


_HAS_STACK = all(
    _installed(module, distribution)
    for module, distribution in (
        ("pyowl_core", "pyowl-core"),
        ("pyelk", "pyelk-reasoner"),
        ("pyhermit", "pyHermiT"),
    )
)


def _encoded_backends_available() -> bool:
    if not _HAS_STACK:
        return False
    try:
        from pyelk.backends import backend_report
        from pyhermit.backends import backend_info
    except (ImportError, AttributeError):
        return False
    return backend_report().rust.available and backend_info().native.available


_HAS_ENCODED_STACK = _encoded_backends_available()


@unittest.skipUnless(_HAS_CORE, "pyowl-core is unavailable")
class TestInstalledNativeOwnerMatrixContract(unittest.TestCase):
    def test_format_and_owner_fixtures_have_identical_public_contracts(self) -> None:
        import pyowl_core

        from oaei_bioml_eval.coherence.bridge import compose_alignment_views
        from tools import installed_native_smoke

        observed: set[
            tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]
        ] = set()
        for format_name, (source_bytes, target_bytes) in (
            installed_native_smoke.FORMAT_FIXTURES.items()
        ):
            with self.subTest(format=format_name):
                source = pyowl_core.coerce_snapshot(
                    source_bytes,
                    document_iri="urn:oaei:test-owner-matrix:source",
                )
                target = pyowl_core.coerce_snapshot(
                    target_bytes,
                    document_iri="urn:oaei:test-owner-matrix:target",
                )
                with installed_native_smoke._owner_pairs(source, target) as pairs:
                    self.assertEqual(
                        set(pairs),
                        {"composite", "decoded", "direct", "mmap", "overlay"},
                    )
                    for owner_name, (owned_source, owned_target) in pairs.items():
                        with self.subTest(format=format_name, owner=owner_name):
                            composite = compose_alignment_views(
                                owned_source,
                                owned_target,
                                (
                                    (
                                        installed_native_smoke.A,
                                        installed_native_smoke.B,
                                        "=",
                                    ),
                                ),
                            )
                            self.assertTrue(
                                installed_native_smoke._retains_owner_leaves(
                                    composite,
                                    owned_source,
                                    owned_target,
                                )
                            )
                            source_fingerprints = installed_native_smoke._fingerprints(
                                owned_source
                            )
                            target_fingerprints = installed_native_smoke._fingerprints(
                                owned_target
                            )
                            observed.add(
                                (
                                    tuple(
                                        sorted(
                                            (
                                                name,
                                                value,
                                            )
                                            for name, value in source_fingerprints.items()
                                            if name != "structural"
                                        )
                                    ),
                                    tuple(
                                        sorted(
                                            (
                                                name,
                                                value,
                                            )
                                            for name, value in target_fingerprints.items()
                                            if name != "structural"
                                        )
                                    ),
                                )
                            )
        self.assertEqual(len(observed), 1)

    def test_strict_matrix_rejects_scalar_incomplete_or_nonzero_handoffs(self) -> None:
        from tools import installed_native_smoke

        counters = {
            name: 0 for name in installed_native_smoke._REQUIRED_PUBLIC_COUNTERS
        }
        counters.update(
            {
                "encoded_buffer_bytes": 4096,
                "encoded_buffer_count": 11,
                "encoded_compiler_gil_released": True,
                "encoded_detached_buffer_count": 11,
                "encoded_referenced_view_count": 2,
                "encoded_segment_count": 3,
                "encoded_zero_copy_buffers": 11,
            }
        )
        handoff = {
            "owner_kind": "composite",
            "core_encoded_view_schemas": {
                installed_native_smoke._ENCODED_SCHEMA: 1
            },
            "reasoner_encoded_schema": dict(
                installed_native_smoke._EXPECTED_REASONER_SCHEMA
            ),
            "ingestion_path": "encoded-native",
            "counters": counters,
        }
        for reasoner in ("elk", "hermit"):
            installed_native_smoke._require_encoded_handoff(
                handoff,
                format_name="functional",
                reasoner=reasoner,
            )

        sidecar = {
            **handoff,
            "counters": {
                **counters,
                "encoded_detached_buffer_count": 14,
                "encoded_indexed_buffer_count": 2,
                "encoded_posting_bytes": 128,
                "encoded_private_ir_bytes": 256,
                "encoded_staging_copy_bytes": 64,
            },
        }
        for reasoner in ("elk", "hermit"):
            installed_native_smoke._require_encoded_handoff(
                sidecar,
                format_name="functional:sidecar",
                reasoner=reasoner,
            )

        scalar = {**handoff, "ingestion_path": "scalar-native"}
        with self.assertRaisesRegex(RuntimeError, "did not select encoded-native"):
            installed_native_smoke._require_encoded_handoff(
                scalar,
                format_name="functional",
                reasoner="elk",
            )

        incompatible_schema = {
            **handoff,
            "reasoner_encoded_schema": {
                **installed_native_smoke._EXPECTED_REASONER_SCHEMA,
                "schema_version": 2,
            },
        }
        with self.assertRaisesRegex(RuntimeError, "exact public reasoner encoded schema"):
            installed_native_smoke._require_encoded_handoff(
                incompatible_schema,
                format_name="functional",
                reasoner="elk",
            )

        incomplete = {**handoff, "counters": {"encoded_buffer_count": 11}}
        with self.assertRaisesRegex(RuntimeError, "counters are incomplete"):
            installed_native_smoke._require_encoded_handoff(
                incomplete,
                format_name="functional",
                reasoner="elk",
            )

        copied = {
            **handoff,
            "counters": {**counters, "structural_copy_bytes": 1},
        }
        with self.assertRaisesRegex(RuntimeError, "forbidden work counters"):
            installed_native_smoke._require_encoded_handoff(
                copied,
                format_name="functional",
                reasoner="elk",
            )
        boolean_zero = {
            **handoff,
            "counters": {**counters, "parser_calls": False},
        }
        with self.assertRaisesRegex(RuntimeError, "counter types"):
            installed_native_smoke._require_encoded_handoff(
                boolean_zero,
                format_name="functional",
                reasoner="elk",
            )
        boolean_zero_copy = {
            **handoff,
            "counters": {
                **counters,
                "encoded_buffer_count": 1,
                "encoded_zero_copy_buffers": True,
            },
        }
        with self.assertRaisesRegex(RuntimeError, "counter types"):
            installed_native_smoke._require_encoded_handoff(
                boolean_zero_copy,
                format_name="functional",
                reasoner="elk",
            )
        for reasoner in ("elk", "hermit"):
            installed_native_smoke._require_encoded_handoff(
                handoff,
                format_name="functional:retry",
                reasoner=reasoner,
            )

    def test_worker_transport_requires_verified_mmap_without_owl_parse(self) -> None:
        from tools import installed_native_smoke

        transport = {
            "mode": "core-wire-worker",
            "wire_verified": True,
            "mmap_verified": True,
            "owl_parse_count": 0,
            "wire_bytes": 4096,
            "wire_sha256": "a" * 64,
        }
        installed_native_smoke._require_worker_transport(
            transport,
            case_name="equivalence_clash",
        )
        invalid_values = (
            ("mode", "in-process-identity", "bounded core-wire worker"),
            ("wire_verified", False, "verify the core wire"),
            ("mmap_verified", False, "verified mmap"),
            ("owl_parse_count", 1, "parsed an OWL document"),
            ("owl_parse_count", False, "parsed an OWL document"),
            ("wire_bytes", 0, "nonempty core wire"),
            ("wire_sha256", "not-a-digest", "canonical wire digest"),
        )
        for field, value, message in invalid_values:
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                installed_native_smoke._require_worker_transport(
                    {**transport, field: value},
                    case_name="equivalence_clash",
                )

    def test_worker_matrix_rejects_invalid_timeout_before_loading_fixtures(self) -> None:
        from tools import installed_native_smoke

        for timeout in (True, 0.0, -1.0, float("inf"), float("nan")):
            with (
                self.subTest(timeout=timeout),
                self.assertRaisesRegex(ValueError, "positive number"),
            ):
                installed_native_smoke.run_worker_regression_matrix(
                    timeout_s=timeout,
                )

    def test_interrupted_owner_matrix_closes_mmaps_and_retry_is_clean(self) -> None:
        import pyowl_core

        from tools import installed_native_smoke

        source_bytes, target_bytes = installed_native_smoke.FORMAT_FIXTURES[
            "functional"
        ]
        source = pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:test-atomicity:source",
        )
        target = pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:test-atomicity:target",
        )
        interrupted_mapped = []
        with (
            self.assertRaises(KeyboardInterrupt),
            installed_native_smoke._owner_pairs(source, target) as pairs,
        ):
            interrupted_mapped.extend(pairs["mmap"])
            raise KeyboardInterrupt
        self.assertTrue(all(owner.closed for owner in interrupted_mapped))

        with installed_native_smoke._owner_pairs(source, target) as retry:
            retried_mapped = tuple(retry["mmap"])
            self.assertEqual(
                installed_native_smoke._fingerprints(retried_mapped[0]),
                installed_native_smoke._fingerprints(source),
            )
            self.assertEqual(
                installed_native_smoke._fingerprints(retried_mapped[1]),
                installed_native_smoke._fingerprints(target),
            )
        self.assertTrue(all(owner.closed for owner in retried_mapped))


@unittest.skipUnless(_HAS_STACK, "installed core, pyELK, and pyHermiT are unavailable")
class TestInstalledNativeOwnerMatrix(unittest.TestCase):
    def test_format_owner_matrix_matches_through_both_public_reasoners(self) -> None:
        from tools import installed_native_smoke

        result = installed_native_smoke.run_format_owner_matrix()

        self.assertEqual(result["schema"], installed_native_smoke.OWNER_MATRIX_SCHEMA)
        self.assertTrue(result["format_owner_semantic_identity"])
        self.assertFalse(result["encoded_required"])
        self.assertEqual(
            set(result["formats"]),
            {"functional", "owlxml", "rdfxml", "turtle"},
        )
        for evidence in result["formats"].values():
            self.assertEqual(
                set(evidence["owners"]),
                {"composite", "decoded", "direct", "mmap", "overlay"},
            )
            for owner in evidence["owners"].values():
                self.assertTrue(owner["composite_owner_identity"])
                self.assertEqual(set(owner["reasoners"]), {"elk", "hermit"})

    @unittest.skipIf(
        os.environ.get("PYELK_PURE_PYTHON") == "1"
        or os.environ.get("PYHERMIT_BACKEND") == "python",
        "advertised encoded-native backends are explicitly disabled",
    )
    @unittest.skipUnless(
        _HAS_ENCODED_STACK,
        "installed portable wheels do not provide both encoded-native backends",
    )
    def test_advertised_owner_matrix_publishes_complete_zero_work_ledgers(self) -> None:
        from tools import installed_native_smoke

        result = installed_native_smoke.run_format_owner_matrix(require_encoded=True)

        self.assertTrue(result["encoded_required"])
        self.assertTrue(result["format_owner_semantic_identity"])
        for evidence in result["formats"].values():
            for owner in evidence["owners"].values():
                for reasoner in owner["reasoners"].values():
                    handoff = reasoner["compiler_handoff"]
                    self.assertEqual(handoff["ingestion_path"], "encoded-native")
                    counters = handoff["counters"]
                    self.assertEqual(
                        {
                            name: counters[name]
                            for name in installed_native_smoke._FORBIDDEN_ZERO_COUNTERS
                        },
                        {
                            name: 0
                            for name in installed_native_smoke._FORBIDDEN_ZERO_COUNTERS
                        },
                    )

    def test_pinned_multi_level_and_control_regressions_match(self) -> None:
        from tools import installed_native_smoke

        result = installed_native_smoke.run_regression_matrix()

        self.assertEqual(
            result["schema"],
            installed_native_smoke.REGRESSION_MATRIX_SCHEMA,
        )
        self.assertTrue(result["pinned_semantic_parity"])
        self.assertFalse(result["encoded_required"])
        self.assertEqual(
            set(result["cases"]),
            {
                "already_incoherent",
                "equivalence_clash",
                "equivalence_clean",
                "subsumption_forward_clash",
                "subsumption_reverse_clash",
            },
        )
        for evidence in result["cases"].values():
            self.assertTrue(evidence["composite_owner_identity"])
            self.assertEqual(set(evidence["reasoners"]), {"elk", "hermit"})

    def test_pinned_elk_regressions_match_through_verified_mmap_worker(self) -> None:
        from tools import installed_native_smoke

        result = installed_native_smoke.run_worker_regression_matrix()

        self.assertEqual(
            result["schema"],
            installed_native_smoke.WORKER_REGRESSION_MATRIX_SCHEMA,
        )
        self.assertTrue(result["worker_mmap_semantic_identity"])
        self.assertFalse(result["encoded_required"])
        self.assertEqual(
            set(result["cases"]),
            {
                "already_incoherent",
                "equivalence_clash",
                "equivalence_clean",
                "subsumption_forward_clash",
                "subsumption_reverse_clash",
            },
        )
        for evidence in result["cases"].values():
            transport = evidence["worker_transport"]
            self.assertEqual(transport["mode"], "core-wire-worker")
            self.assertTrue(transport["wire_verified"])
            self.assertTrue(transport["mmap_verified"])
            self.assertEqual(transport["owl_parse_count"], 0)


if __name__ == "__main__":
    unittest.main()
