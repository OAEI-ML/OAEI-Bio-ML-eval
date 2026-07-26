"""Executable contract tests for the installed native owner matrix."""

from __future__ import annotations

import importlib.metadata
import importlib.util
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
                        {"decoded", "direct", "mmap", "overlay"},
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
                            self.assertIs(composite.members[0].view, owned_source)
                            self.assertIs(composite.members[1].view, owned_target)
                            observed.add(
                                (
                                    tuple(
                                        sorted(
                                            installed_native_smoke._fingerprints(
                                                owned_source
                                            ).items()
                                        )
                                    ),
                                    tuple(
                                        sorted(
                                            installed_native_smoke._fingerprints(
                                                owned_target
                                            ).items()
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
                "encoded_buffer_count": 11,
                "encoded_zero_copy_buffers": 11,
                "materialized_scalar_rows": 0,
                "structural_copy_bytes": 0,
            }
        )
        handoff = {
            "owner_kind": "composite",
            "core_encoded_view_schemas": {
                installed_native_smoke._ENCODED_SCHEMA: 1
            },
            "ingestion_path": "encoded-native",
            "counters": counters,
        }
        installed_native_smoke._require_encoded_handoff(
            handoff,
            format_name="functional",
            reasoner="elk",
        )

        scalar = {**handoff, "ingestion_path": "scalar-native"}
        with self.assertRaisesRegex(RuntimeError, "did not select encoded-native"):
            installed_native_smoke._require_encoded_handoff(
                scalar,
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
                {"decoded", "direct", "mmap", "overlay"},
            )
            for owner in evidence["owners"].values():
                self.assertTrue(owner["composite_owner_identity"])
                self.assertEqual(set(owner["reasoners"]), {"elk", "hermit"})

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


if __name__ == "__main__":
    unittest.main()
