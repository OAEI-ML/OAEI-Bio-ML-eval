"""O3 native-adapter contracts using deterministic public-protocol doubles."""

from __future__ import annotations

import json
import os
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from oaei_bioml_eval.coherence.bridge import (
    analyze_correspondences,
    compose_alignment_views,
)
from oaei_bioml_eval.coherence.native_reasoners import (
    CoreWireEnvelope,
    ELKReasoner,
    ELKTimeoutError,
    ELKWorkerResult,
    HermiTReasoner,
    HermiTTimeoutError,
    NativeReasoner,
    NativeReasonerCompatibilityError,
    NativeReasonerUnavailableError,
    NativeWorkerError,
    _encode_core_wire,
    _run_elk_worker,
)
from oaei_bioml_eval.coherence.provenance import (
    build_coherence_provenance,
    canonical_provenance_json,
    sorted_line_sha256,
)
from oaei_bioml_eval.coherence.reasoner import CoherenceReasoner, UnsatResult
from oaei_bioml_eval.coherence.report import score_global_coherence
from tools.native_compare import compare_report
from tools.native_fixture_compare import compare_case

A, B, C, D = (f"http://ex.org/{value}" for value in "ABCD")
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"

try:
    import pyowl_core as _pyowl_core
except ImportError:
    _pyowl_core = None


class _IRI:
    def __init__(self, value: str) -> None:
        self.value = value


class _Class:
    def __init__(self, value: str) -> None:
        self.iri = _IRI(value)


class _Backend:
    name = "python"
    package_version = "0.1.0.dev0"
    implementation_version = "test-double-v1"
    ir_schema_version = 1
    accelerated = False
    complete_features = frozenset({"classification"})


class _HermiTConfig:
    last_timeout = None

    def __init__(self, *, timeout=None) -> None:
        type(self).last_timeout = timeout


class _HermiTInconsistentError(Exception):
    pass


class _HermiTTimeout(TimeoutError):
    pass


class _HermiTSession:
    mode = "consistent"
    last_ontology = None
    disposed = False

    def __init__(self, ontology, *, config) -> None:
        del config
        type(self).last_ontology = ontology
        type(self).disposed = False
        self.ontology = ontology
        self.backend = _Backend()

    def is_consistent(self):
        if type(self).mode == "timeout":
            raise _HermiTTimeout("cooperative gate")
        if type(self).mode == "failure":
            raise RuntimeError("semantic failure")
        return type(self).mode != "inconsistent-bool"

    def unsatisfiable_classes(self):
        if type(self).mode == "inconsistent-error":
            raise _HermiTInconsistentError("classical explosion")
        return frozenset({_Class(A), _Class(B), _Class(OWL_NOTHING)})

    def dispose(self):
        type(self).disposed = True


class _ELKConfig:
    pass


class _ReasoningResult:
    def __init__(self, value, *, complete=True, reasons=()) -> None:
        self.value = value
        self.complete = complete
        self.reasons = reasons


class _Node:
    def __init__(self, values) -> None:
        self.members = tuple(_Class(value) for value in values)


class _Taxonomy:
    def __init__(self, bottom) -> None:
        self.bottom = _Node(bottom)


class _Task:
    value = "class_taxonomy"


class _CompletenessIssue:
    task = _Task()
    features = ("DISJOINT_UNION",)
    constructors = ("DisjointUnion",)
    polarities = ("ANY",)


class _ELKSession:
    last_ontology = None
    closed = False
    inconsistent = False
    reasons = ()

    def __init__(self, ontology, config) -> None:
        del config
        type(self).last_ontology = ontology
        type(self).closed = False
        self.ontology = ontology
        self.backend = _Backend()

    def is_consistent(self):
        return _ReasoningResult(not type(self).inconsistent)

    def classify(self):
        return _ReasoningResult(
            _Taxonomy((A, B, OWL_NOTHING)),
            complete=not type(self).reasons,
            reasons=type(self).reasons,
        )

    def close(self):
        type(self).closed = True


def _pyhermit_module() -> types.ModuleType:
    module = types.ModuleType("pyhermit")
    module.__version__ = "0.1.0.dev0"
    module.Reasoner = _HermiTSession
    module.ReasonerConfig = _HermiTConfig
    module.InconsistentOntologyError = _HermiTInconsistentError
    module.ReasonerTimeoutError = _HermiTTimeout
    return module


def _pyelk_module() -> types.ModuleType:
    module = types.ModuleType("pyelk")
    module.__version__ = "0.1.0.dev0"
    module.Reasoner = _ELKSession
    module.ReasonerConfig = _ELKConfig
    return module


def _success_worker(connection, payload, fingerprints):
    response = {
        "ok": True,
        "result": {
            "unsatisfiable": [A, B],
            "inconsistent": False,
            "reasoner": {
                "name": "python",
                "package_version": "0.1.0.dev0",
            },
            "profile": {"complete": True, "reasons": []},
            "wire_verified": fingerprints == {"logical_fingerprint": "a" * 64},
            "owl_parse_count": 0,
            "payload_sha256": __import__("hashlib").sha256(payload).hexdigest(),
        },
    }
    connection.send_bytes(json.dumps(response, sort_keys=True).encode("utf-8"))
    connection.close()


def _sleep_worker(connection, payload, fingerprints):
    del payload, fingerprints
    time.sleep(5)
    connection.close()


def _silent_worker(connection, payload, fingerprints):
    del payload, fingerprints
    connection.close()


class TestCapabilityBoundary(unittest.TestCase):
    def test_missing_reasoner_is_actionable(self):
        error = ModuleNotFoundError("no pyhermit", name="pyhermit")
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            side_effect=error,
        ), self.assertRaises(NativeReasonerUnavailableError):
            HermiTReasoner().unsatisfiable_classes_view(
                object(), which="hermit", timeout_s=1.0
            )

    def test_incomplete_frozen_facade_fails_without_fallback(self):
        module = types.ModuleType("pyhermit")
        module.__version__ = "0.1.0.dev0"
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=module,
        ), self.assertRaisesRegex(
            NativeReasonerCompatibilityError, "frozen public facade"
        ):
            HermiTReasoner().unsatisfiable_classes_view(
                object(), which="hermit", timeout_s=1.0
            )

    def test_incompatible_reasoner_version_fails_explicitly(self):
        module = _pyhermit_module()
        module.__version__ = "0.2.0"
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=module,
        ), self.assertRaisesRegex(
            NativeReasonerCompatibilityError, "expected >=0.1,<0.2"
        ):
            HermiTReasoner().unsatisfiable_classes_view(
                object(), which="hermit", timeout_s=1.0
            )

    def test_pyelk_uses_the_distribution_name_not_the_import_name(self):
        module = _pyelk_module()
        del module.__version__
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.metadata.version",
            return_value="0.1.0.dev0",
        ) as version, mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=module,
        ):
            result = ELKReasoner().unsatisfiable_classes_view(
                object(), which="elk", timeout_s=None
            )
        version.assert_called_once_with("pyelk-reasoner")
        self.assertEqual(result.provenance["package_version"], "0.1.0.dev0")


_DEFAULT_ONTOLOGY = object()


class TestHermiTAdapter(unittest.TestCase):
    def setUp(self):
        _HermiTSession.mode = "consistent"

    def _classify(self, ontology=_DEFAULT_ONTOLOGY):
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=_pyhermit_module(),
        ):
            return HermiTReasoner().unsatisfiable_classes_view(
                ontology, which="hermit", timeout_s=2.5
            )

    def test_exact_identity_timeout_config_and_unsatisfiable_set(self):
        ontology = object()
        result = self._classify(ontology)
        self.assertIs(_HermiTSession.last_ontology, ontology)
        self.assertEqual(_HermiTConfig.last_timeout, 2.5)
        self.assertEqual(result.unsatisfiable, (A, B))
        self.assertFalse(result.inconsistent)
        self.assertEqual(result.provenance["transport"], "in-process-identity")
        self.assertTrue(_HermiTSession.disposed)

    def test_both_inconsistency_forms_become_full_signature(self):
        for mode in ("inconsistent-bool", "inconsistent-error"):
            with self.subTest(mode=mode), mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.named_class_iris",
                return_value=(A, B, C, D),
            ):
                _HermiTSession.mode = mode
                result = self._classify()
                self.assertEqual(result.unsatisfiable, (A, B, C, D))
                self.assertTrue(result.inconsistent)

    def test_only_public_timeout_maps_to_fallback_signal(self):
        _HermiTSession.mode = "timeout"
        with self.assertRaises(HermiTTimeoutError):
            self._classify()
        _HermiTSession.mode = "failure"
        with self.assertRaisesRegex(RuntimeError, "semantic failure"):
            self._classify()


class TestELKAdapter(unittest.TestCase):
    def setUp(self):
        _ELKSession.inconsistent = False
        _ELKSession.reasons = ()

    def test_unbounded_call_retains_identity_and_public_result_shapes(self):
        ontology = object()
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=_pyelk_module(),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(
                ontology, which="elk", timeout_s=None
            )
        self.assertIs(_ELKSession.last_ontology, ontology)
        self.assertEqual(result.unsatisfiable, (A, B))
        self.assertEqual(result.provenance["transport"]["mode"], "in-process-identity")
        self.assertTrue(_ELKSession.closed)

    def test_bounded_call_uses_only_verified_wire_result(self):
        ontology = object()
        envelope = CoreWireEnvelope(
            b"core-wire",
            {"logical_fingerprint": "a" * 64},
            (1, 0),
            "0.1.0.dev0",
        )
        outcome = ELKWorkerResult(
            (A,),
            False,
            {"name": "python", "package_version": "0.1.0.dev0"},
            {"complete": True, "reasons": []},
            True,
            0,
        )
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
            return_value=object(),
        ), mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._encode_core_wire",
            return_value=envelope,
        ) as encode, mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._run_elk_worker",
            return_value=outcome,
        ) as worker:
            result = ELKReasoner().unsatisfiable_classes_view(
                ontology, which="elk", timeout_s=3.0
            )
        encode.assert_called_once_with(ontology)
        worker.assert_called_once_with(envelope, timeout_s=3.0)
        self.assertEqual(result.unsatisfiable, (A,))
        transport = result.provenance["transport"]
        self.assertTrue(transport["wire_verified"])
        self.assertEqual(transport["owl_parse_count"], 0)

    def test_bounded_call_requires_the_frozen_core_wire_api(self):
        incomplete_core = types.ModuleType("pyowl_core")
        incomplete_core.WIRE_FORMAT_VERSION = (1, 0)
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
            return_value=object(),
        ), mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=incomplete_core,
        ), self.assertRaisesRegex(
            NativeReasonerCompatibilityError, "wire contract"
        ):
            ELKReasoner().unsatisfiable_classes_view(
                object(), which="elk", timeout_s=1.0
            )

    def test_unverified_or_reparsed_worker_result_is_rejected(self):
        envelope = CoreWireEnvelope(b"wire", {}, (1, 0), "0.1.0.dev0")
        outcome = ELKWorkerResult(
            (),
            False,
            {"package_version": "0.1.0.dev0"},
            {"complete": True, "reasons": []},
            False,
            1,
        )
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
            return_value=object(),
        ), mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._encode_core_wire",
            return_value=envelope,
        ), mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners._run_elk_worker",
            return_value=outcome,
        ), self.assertRaisesRegex(NativeWorkerError, "zero OWL parses"):
            ELKReasoner().unsatisfiable_classes_view(
                object(), which="elk", timeout_s=1.0
            )

    def test_inconsistent_elk_result_expands_to_the_full_signature(self):
        _ELKSession.inconsistent = True
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.named_class_iris",
            return_value=(A, B, C, D),
        ), mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=_pyelk_module(),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(
                object(), which="elk", timeout_s=None
            )
        self.assertTrue(result.inconsistent)
        self.assertEqual(result.unsatisfiable, (A, B, C, D))

    def test_profile_limitations_are_preserved_in_provenance(self):
        _ELKSession.reasons = (_CompletenessIssue(),)
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=_pyelk_module(),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(
                object(), which="elk", timeout_s=None
            )
        profile = result.provenance["profile"]
        self.assertFalse(profile["complete"])
        self.assertEqual(profile["reasons"][0]["features"], ["DISJOINT_UNION"])


class TestWireWorker(unittest.TestCase):
    envelope = CoreWireEnvelope(
        b"wire-payload",
        {"logical_fingerprint": "a" * 64},
        (1, 0),
        "0.1.0.dev0",
    )

    def test_success_is_canonical_small_result(self):
        result = _run_elk_worker(
            self.envelope, timeout_s=5.0, entrypoint=_success_worker
        )
        self.assertEqual(result.unsatisfiable, (A, B))
        self.assertTrue(result.wire_verified)
        self.assertEqual(result.owl_parse_count, 0)

    def test_timeout_terminates_worker(self):
        started = time.perf_counter()
        with self.assertRaises(ELKTimeoutError):
            _run_elk_worker(
                self.envelope, timeout_s=0.05, entrypoint=_sleep_worker
            )
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_worker_exit_is_error_not_a_semantic_value(self):
        with self.assertRaises(NativeWorkerError):
            _run_elk_worker(
                self.envelope, timeout_s=5.0, entrypoint=_silent_worker
            )

    def test_envelope_records_the_emitted_wire_minor_not_the_capability_max(self):
        core = types.ModuleType("pyowl_core")
        core.__version__ = "0.1.0.dev0"
        core.WIRE_FORMAT_VERSION = (1, 1)

        def encode_snapshot(ontology):
            del ontology
            return b"PYOCORE\0\x01\x00\x00\x00"

        def decode_snapshot(payload):
            return payload

        core.encode_snapshot = encode_snapshot
        core.decode_snapshot = decode_snapshot

        class Fingerprint:
            hex = "a" * 64

        ontology = types.SimpleNamespace(
            structural_fingerprint=Fingerprint(),
            logical_fingerprint=Fingerprint(),
            signature_fingerprint=Fingerprint(),
        )
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=core,
        ):
            envelope = _encode_core_wire(ontology)
        self.assertEqual(envelope.wire_version, (1, 0))


class _RecordingAdapter(CoherenceReasoner):
    def __init__(self, *, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def unsatisfiable_classes_view(self, ontology, *, which, timeout_s):
        self.calls.append((ontology, which, timeout_s))
        if self.error is not None:
            raise self.error
        return self.result


class _Composite:
    def __init__(self, source, target, pairs) -> None:
        self.source = source
        self.target = target
        self.pairs = tuple(pairs)


class TestGateAndProvenance(unittest.TestCase):
    def _score(self, hermit, elk):
        router = NativeReasoner(hermit=hermit, elk=elk)
        with mock.patch(
            "oaei_bioml_eval.coherence.report.load_reasoner", return_value=router
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.compose_alignment_views",
            side_effect=_Composite,
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.named_class_iris",
            return_value=(A, B),
        ):
            return score_global_coherence([(A, B)], object(), object(), timeout_s=1.0)

    def test_timeout_only_falls_back_on_same_composite(self):
        hermit = _RecordingAdapter(
            error=HermiTTimeoutError("timeout", elapsed_seconds=0.5)
        )
        elk = _RecordingAdapter(result=UnsatResult((A,), "elk", 0.01))
        report = self._score(hermit, elk)
        self.assertIs(hermit.calls[0][0], elk.calls[0][0])
        self.assertEqual(report["reasoner_used"], "elk")
        provenance = report["provenance"]
        self.assertEqual(provenance["reasoner"]["fallback_reason"], "hermit-timeout")
        self.assertAlmostEqual(provenance["reasoner"]["elapsed_seconds"], 0.51)

    def test_timeout_attempt_metadata_reaches_final_provenance(self):
        hermit = _RecordingAdapter(
            error=HermiTTimeoutError(
                "timeout",
                elapsed_seconds=0.5,
                attempt={
                    "status": "timeout",
                    "package": "pyHermiT",
                    "package_version": "0.1.0.dev0",
                    "backend": {"name": "python"},
                },
            )
        )
        elk = _RecordingAdapter(result=UnsatResult((A,), "elk", 0.01))
        report = self._score(hermit, elk)
        attempt = report["provenance"]["reasoner"]["prior_attempts"][0]
        self.assertEqual(attempt["status"], "timeout")
        self.assertEqual(attempt["package"], "pyHermiT")
        self.assertEqual(attempt["backend"]["name"], "python")

    def test_other_timeout_class_and_other_errors_do_not_fallback(self):
        for error in (TimeoutError("not pyHermiT"), RuntimeError("failure")):
            with self.subTest(error=type(error).__name__):
                hermit = _RecordingAdapter(error=error)
                elk = _RecordingAdapter(result=UnsatResult((A,), "elk", 0.01))
                with self.assertRaises(type(error)):
                    self._score(hermit, elk)
                self.assertEqual(elk.calls, [])

    def test_inconsistent_result_is_one_and_versioned(self):
        hermit = _RecordingAdapter(
            result=UnsatResult((A, B), "hermit", 0.01, inconsistent=True)
        )
        report = self._score(hermit, _RecordingAdapter())
        self.assertEqual(report["global_coherence"], 1.0)
        self.assertTrue(report["inconsistent"])
        self.assertEqual(report["provenance"]["schema"], "coherence-provenance/1")
        self.assertTrue(report["provenance"]["reasoner"]["inconsistent"])

    def test_provenance_is_deterministic_and_contains_no_local_identity(self):
        bridge = analyze_correspondences(
            [(A, B), (A, B, "equiv"), (A, A)], invalid_dropped_count=2
        )
        result = UnsatResult((A,), "hermit", 0.25)
        ontology = _Composite(object(), object(), bridge.correspondences)
        first = build_coherence_provenance(
            ontology,
            bridge,
            (A, B),
            result,
            requested_reasoner="hermit",
            timeout_s=1.0,
            api_options={"metric": "global"},
        )
        second = build_coherence_provenance(
            ontology,
            bridge,
            (A, B),
            result,
            requested_reasoner="hermit",
            timeout_s=1.0,
            api_options={"metric": "global"},
        )
        self.assertEqual(canonical_provenance_json(first), canonical_provenance_json(second))
        encoded = canonical_provenance_json(first)
        self.assertNotIn(os.getcwd(), encoded)
        self.assertNotIn("object at 0x", encoded)
        self.assertNotIn("timestamp", encoded)
        self.assertEqual(first["bridge"]["normalized_count"], 1)
        self.assertEqual(first["bridge"]["duplicate_count"], 1)
        self.assertEqual(first["bridge"]["self_pair_count"], 1)
        self.assertEqual(first["bridge"]["invalid_dropped_count"], 2)


class TestDeferredRealDataComparison(unittest.TestCase):
    def test_public_ncit_doid_comparator_checks_all_frozen_semantics(self):
        baseline = json.loads(
            (
                Path(__file__).parent
                / "baselines"
                / "robot-1.9.10-ncit-doid-train.json"
            ).read_text(encoding="utf-8")
        )
        expected = baseline["runs"]["elk"]
        report = {
            "reasoner_used": "elk",
            "union_class_count": baseline["inputs"]["named_class_count"],
            "asserted_correspondences": baseline["bridge"]["mapping_count"],
            "unsatisfiable_count": expected["unsatisfiable_count"],
            "provenance": {
                "schema": "coherence-provenance/1",
                "result": {
                    "numerator_sha256": expected["unsatisfiable_sha256"]
                },
            },
        }
        comparison = compare_report(report, baseline, reasoner="elk")
        self.assertTrue(comparison["agreement"])
        changed = dict(report)
        changed["unsatisfiable_count"] = expected["unsatisfiable_count"] + 1
        with self.assertRaisesRegex(RuntimeError, "differs"):
            compare_report(changed, baseline, reasoner="elk")

    def test_small_fixture_comparator_covers_every_pinned_case_and_backend(self):
        baseline = json.loads(
            (
                Path(__file__).parent / "baselines" / "robot-1.9.10.json"
            ).read_text(encoding="utf-8")
        )
        for case_id, expected in baseline["cases"].items():
            for reasoner in ("hermit", "elk"):
                with self.subTest(case=case_id, reasoner=reasoner):
                    unsatisfiable = expected[f"{reasoner}_unsatisfiable"]
                    report = {
                        "reasoner_used": reasoner,
                        "union_class_count": len(expected["named_classes"]),
                        "unsatisfiable_count": len(unsatisfiable),
                        "provenance": {
                            "result": {
                                "numerator_sha256": sorted_line_sha256(
                                    unsatisfiable
                                )
                            }
                        },
                    }
                    compare_case(report, expected, reasoner=reasoner)


@unittest.skipUnless(_pyowl_core is not None, "pyowl-core not installed")
class TestConcreteCoreProvenance(unittest.TestCase):
    def test_composite_manifest_and_fingerprints_are_public_and_stable(self):
        assert _pyowl_core is not None
        source = _pyowl_core.coerce_snapshot(
            (
                "Ontology(<urn:source> "
                f"Declaration(Class(<{A}>)) Declaration(Class(<{C}>)))"
            ).encode(),
            document_iri="urn:document:source",
        )
        target = _pyowl_core.coerce_snapshot(
            (
                "Ontology(<urn:target> "
                f"Declaration(Class(<{B}>)) Declaration(Class(<{D}>)))"
            ).encode(),
            document_iri="urn:document:target",
        )
        bridge = analyze_correspondences([(A, B)])
        composite = compose_alignment_views(source, target, bridge.correspondences)
        result = UnsatResult((A,), "hermit", 0.1)
        provenance = build_coherence_provenance(
            composite,
            bridge,
            (A, B, C, D),
            result,
            requested_reasoner="hermit",
            timeout_s=1.0,
        )
        core = provenance["core"]
        self.assertEqual(
            core["fingerprints"]["logical"], composite.logical_fingerprint.hex
        )
        self.assertEqual([item["role"] for item in core["roles"]], ["source", "target"])
        self.assertEqual(len(core["documents"]), 2)
        encoded = canonical_provenance_json(provenance)
        self.assertNotIn("urn:document:source", encoded)
        self.assertNotIn("urn:document:target", encoded)


if __name__ == "__main__":
    unittest.main()
