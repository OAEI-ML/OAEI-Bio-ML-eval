"""Java-free semantic integration against installed sibling reasoner facades."""

from __future__ import annotations

import importlib.metadata
import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from oaei_bioml_eval.coherence.bridge import Correspondence
from oaei_bioml_eval.coherence.native_reasoners import (
    HermiTReasoner,
    HermiTTimeoutError,
)
from oaei_bioml_eval.coherence.provenance import sorted_line_sha256
from oaei_bioml_eval.coherence.report import ReasonerName, score_reference_coherence

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
A = "http://ex.org/A"
B = "http://ex.org/B"


def _available(module_name: str, distribution: str, exports: tuple[str, ...]) -> bool:
    try:
        module = __import__(module_name)
        importlib.metadata.version(distribution)
    except (ImportError, importlib.metadata.PackageNotFoundError):
        return False
    return all(hasattr(module, name) for name in exports)


try:
    import pyowl_core as _pyowl_core
except ImportError:  # pragma: no cover - optional integration environment
    _pyowl_core = None  # type: ignore[assignment]

pyowl_core: Any = _pyowl_core

_HAS_ELK = pyowl_core is not None and _available(
    "pyelk", "pyelk-reasoner", ("Reasoner", "ReasonerConfig")
)
_HAS_HERMIT = pyowl_core is not None and _available(
    "pyhermit",
    "pyHermiT",
    (
        "Reasoner",
        "ReasonerConfig",
        "InconsistentOntologyError",
        "ReasonerTimeoutError",
    ),
)


def _views(
    case_id: str, expected: Mapping[str, object]
) -> tuple[Any, Any]:
    assert _pyowl_core is not None
    source = pyowl_core.coerce_snapshot(
        FIXTURES / str(expected["source"]),
        document_iri=f"urn:oaei:integration:{case_id}:source",
    )
    target = pyowl_core.coerce_snapshot(
        FIXTURES / str(expected["target"]),
        document_iri=f"urn:oaei:integration:{case_id}:target",
    )
    return source, target


def _assert_case(
    test: unittest.TestCase,
    case_id: str,
    expected: Mapping[str, object],
    *,
    reasoner: str,
    timeout_s: float | None,
) -> Mapping[str, object]:
    source, target = _views(case_id, expected)
    bridge = [
        cast(Correspondence, tuple(item))
        for item in cast(list[list[str]], expected["bridge"])
    ]
    selected_reasoner = cast(ReasonerName, reasoner)
    report = score_reference_coherence(
        bridge,
        source,
        target,
        reasoner=selected_reasoner,
        timeout_s=timeout_s,
    )
    wanted_unsatisfiable = cast(list[str], expected[f"{reasoner}_unsatisfiable"])
    named_classes = cast(list[str], expected["named_classes"])
    test.assertEqual(report["reasoner_used"], reasoner)
    test.assertEqual(report["union_class_count"], len(named_classes))
    test.assertEqual(report["unsatisfiable_count"], len(wanted_unsatisfiable))
    provenance = report["provenance"]
    test.assertIsInstance(provenance, dict)
    assert isinstance(provenance, dict)
    result = cast(dict[str, object], provenance["result"])
    test.assertEqual(
        result["numerator_sha256"],
        sorted_line_sha256(wanted_unsatisfiable),
    )
    return report


class _FixtureMixin:
    reasoner: str

    def test_all_pinned_fixtures_match_in_process(self) -> None:
        test = cast(unittest.TestCase, self)
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for case_id, expected in sorted(baseline["cases"].items()):
            with test.subTest(case=case_id):
                report = _assert_case(
                    test,
                    case_id,
                    expected,
                    reasoner=self.reasoner,
                    timeout_s=None,
                )
                provenance = cast(dict[str, object], report["provenance"])
                reasoner = cast(dict[str, object], provenance["reasoner"])
                transport = cast(dict[str, object], reasoner["transport"])
                test.assertEqual(transport["mode"], "in-process-identity")

    def test_inconsistent_view_uses_classical_explosion(self) -> None:
        test = cast(unittest.TestCase, self)
        assert _pyowl_core is not None
        source = pyowl_core.coerce_snapshot(
            (
                "Ontology(<urn:oaei:inconsistent> "
                f"Declaration(Class(<{A}>)) "
                "Declaration(NamedIndividual(<urn:oaei:inconsistent:witness>)) "
                "ClassAssertion(<http://www.w3.org/2002/07/owl#Nothing> "
                "<urn:oaei:inconsistent:witness>))"
            ).encode()
        )
        target = pyowl_core.coerce_snapshot(
            f"Ontology(<urn:oaei:target> Declaration(Class(<{B}>)))".encode()
        )
        report = score_reference_coherence(
            (),
            source,
            target,
            reasoner=cast(ReasonerName, self.reasoner),
            timeout_s=None,
        )
        test.assertEqual(report["global_coherence"], 1.0)
        test.assertEqual(report["unsatisfiable_count"], 2)
        test.assertEqual(report["union_class_count"], 2)
        test.assertTrue(report["inconsistent"])
        provenance = cast(dict[str, object], report["provenance"])
        reasoner = cast(dict[str, object], provenance["reasoner"])
        test.assertTrue(reasoner["inconsistent"])


@unittest.skipUnless(_HAS_ELK, "installed pyELK public facade is unavailable")
class TestConcreteELKIntegration(_FixtureMixin, unittest.TestCase):
    reasoner = "elk"

    def test_bounded_call_uses_verified_wire_without_owl_parse(self) -> None:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        expected = baseline["cases"]["equivalence_clash"]
        report = _assert_case(
            self,
            "equivalence_clash",
            expected,
            reasoner="elk",
            timeout_s=30.0,
        )
        transport = report["provenance"]["reasoner"]["transport"]  # type: ignore[index]
        self.assertEqual(transport["mode"], "core-wire-worker")
        self.assertTrue(transport["wire_verified"])
        self.assertTrue(transport["mmap_verified"])
        self.assertEqual(transport["owl_parse_count"], 0)
        self.assertGreater(transport["wire_bytes"], 0)


@unittest.skipUnless(_HAS_HERMIT, "installed pyHermiT public facade is unavailable")
class TestConcreteHermiTIntegration(_FixtureMixin, unittest.TestCase):
    reasoner = "hermit"

    def test_public_cooperative_timeout_maps_to_oaei_fallback_signal(self) -> None:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        expected = baseline["cases"]["equivalence_clash"]
        source, target = _views("timeout", expected)
        from oaei_bioml_eval.coherence.bridge import compose_alignment_views

        composite = compose_alignment_views(
            source,
            target,
            [tuple(item) for item in expected["bridge"]],
        )
        with self.assertRaises(HermiTTimeoutError) as raised:
            HermiTReasoner().unsatisfiable_classes_view(
                composite,
                which="hermit",
                timeout_s=1e-12,
            )
        self.assertGreater(raised.exception.elapsed_seconds, 0.0)
        self.assertEqual(raised.exception.attempt["status"], "timeout")
        self.assertEqual(raised.exception.attempt["package"], "pyHermiT")


if __name__ == "__main__":
    unittest.main()
