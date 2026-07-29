"""O3 native-adapter contracts using deterministic public-protocol doubles."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
    _backend_metadata,
    _decode_worker_response,
    _elk_worker_entry,
    _encode_core_wire,
    _run_elk_worker,
    _validate_encoded_session_handoff,
)
from oaei_bioml_eval.coherence.provenance import (
    build_coherence_provenance,
    canonical_provenance_json,
    sorted_line_sha256,
)
from oaei_bioml_eval.coherence.reasoner import CoherenceReasoner, UnsatResult
from oaei_bioml_eval.coherence.report import score_global_coherence
from tools import native_compare
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


_ENCODED_BUFFER_WIDTHS = {
    "root_kinds": 1,
    "root_ids": 4,
    "node_tags": 2,
    "node_field_offsets": 8,
    "field_kinds": 1,
    "field_values": 8,
    "field_lengths": 8,
    "item_kinds": 1,
    "item_values": 8,
    "item_lengths": 8,
    "scalar_bytes": 1,
}


def _compiler_handoff():
    return {
        "schema_name": "pyowl-core/structural-columns",
        "schema_version": 1,
        "model_schema": 1,
        "descriptor_sha256": (
            "9ad29db6a7e616f65cea2957bc5ba8d1f9b99ef0eb1fe1432c09be25786267b5"
        ),
        "buffer_widths": dict(_ENCODED_BUFFER_WIDTHS),
    }


def _encoded_counters(**overrides):
    counters = {
        "base_flattening_bytes": 0,
        "encoded_buffer_bytes": 1024,
        "encoded_buffer_count": 11,
        "encoded_compiler_gil_released": True,
        "encoded_detached_buffer_count": 11,
        "encoded_indexed_buffer_count": 0,
        "encoded_posting_bytes": 0,
        "encoded_private_ir_bytes": 0,
        "encoded_referenced_view_count": 2,
        "encoded_segment_count": 3,
        "encoded_staging_copy_bytes": 0,
        "encoded_zero_copy_buffers": 11,
        "materialized_scalar_rows": 0,
        "parser_calls": 0,
        "per_row_ffi_calls": 0,
        "resolver_calls": 0,
        "scalar_axiom_materializations": 0,
        "scalar_term_materializations": 0,
        "structural_copy_bytes": 0,
        "wire_decoder_calls": 0,
        "wire_encoder_calls": 0,
    }
    counters.update(overrides)
    return counters


class _CoreContract:
    package_version = "0.1.0.dev0"
    api_version = (0, 1)
    adapter_protocol = 1
    model_schema = 1
    wire_format = (1, 1)

    @classmethod
    def current(cls):
        return cls()


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


def _success_worker(connection, wire_path, fingerprints):
    payload = Path(wire_path).read_bytes()
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
            "mmap_verified": True,
            "owl_parse_count": 0,
            "payload_sha256": __import__("hashlib").sha256(payload).hexdigest(),
        },
    }
    connection.send_bytes(json.dumps(response, sort_keys=True).encode("utf-8"))
    connection.close()


def _sleep_worker(connection, wire_path, fingerprints):
    del wire_path, fingerprints
    time.sleep(5)
    connection.close()


def _silent_worker(connection, wire_path, fingerprints):
    del wire_path, fingerprints
    connection.close()


def _success_compare_worker(connection, request):
    response = {
        "ok": True,
        "result": {
            "agreement": True,
            "reasoner": request.reasoner,
        },
    }
    connection.send_bytes(json.dumps(response, sort_keys=True).encode("utf-8"))
    connection.close()


def _sleep_compare_worker(connection, request):
    del request
    time.sleep(5)
    connection.close()


class TestCapabilityBoundary(unittest.TestCase):
    def test_missing_reasoner_is_actionable(self):
        error = ModuleNotFoundError("no pyhermit", name="pyhermit")
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                side_effect=error,
            ),
            self.assertRaises(NativeReasonerUnavailableError),
        ):
            HermiTReasoner().unsatisfiable_classes_view(object(), which="hermit", timeout_s=1.0)

    def test_incomplete_frozen_facade_fails_without_fallback(self):
        module = types.ModuleType("pyhermit")
        module.__version__ = "0.1.0.dev0"
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=module,
            ),
            self.assertRaisesRegex(NativeReasonerCompatibilityError, "frozen public facade"),
        ):
            HermiTReasoner().unsatisfiable_classes_view(object(), which="hermit", timeout_s=1.0)

    def test_incompatible_reasoner_version_fails_explicitly(self):
        module = _pyhermit_module()
        module.__version__ = "0.2.0"
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=module,
            ),
            self.assertRaisesRegex(NativeReasonerCompatibilityError, "expected >=0.1,<0.2"),
        ):
            HermiTReasoner().unsatisfiable_classes_view(object(), which="hermit", timeout_s=1.0)

    def test_module_and_installed_distribution_version_drift_fails_closed(self):
        module = _pyelk_module()
        module.__version__ = "0.1.0.dev0"
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.metadata.version",
                return_value="0.1.1",
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=module,
            ),
            self.assertRaisesRegex(
                NativeReasonerCompatibilityError,
                "module/distribution version mismatch",
            ),
        ):
            ELKReasoner().unsatisfiable_classes_view(
                object(),
                which="elk",
                timeout_s=None,
            )

    def test_pyelk_uses_the_distribution_name_not_the_import_name(self):
        module = _pyelk_module()
        del module.__version__
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.metadata.version",
                return_value="0.1.0.dev0",
            ) as version,
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=module,
            ),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(object(), which="elk", timeout_s=None)
        version.assert_called_once_with("pyelk-reasoner")
        self.assertEqual(result.provenance["package_version"], "0.1.0.dev0")

    def test_absent_compiler_handoff_is_a_scalar_compatible_noop(self):
        metadata = _backend_metadata(types.SimpleNamespace(backend=_Backend()), "0.1.0.dev0")
        self.assertNotIn("compiler_handoff", metadata)
        self.assertNotIn("compiler_diagnostics", metadata)

    def test_encoded_session_negotiates_exact_public_core_and_reasoner_schemas(self):
        class Requirement:
            def __init__(self, **values):
                self.values = values

        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        require_compatible = mock.Mock(side_effect=lambda view, _requirement: view)
        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=require_compatible,
        )
        metadata = {
            "name": "native",
            "accelerated": True,
            "core_api_version": [0, 1],
            "core_adapter_protocol_version": 1,
            "core_model_schema_version": 1,
            "core_package_version": "0.1.0.dev0",
            "core_wire_format_version": [1, 1],
            "compiler_handoff": _compiler_handoff(),
            "compiler_diagnostics": {
                "ingestion_path": "encoded-native",
                "counters": _encoded_counters(),
            },
        }

        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=adapters,
        ) as importer:
            _validate_encoded_session_handoff(ontology, metadata)

        importer.assert_called_once_with("pyowl_core.adapters")
        requirement = require_compatible.call_args.args[1]
        self.assertEqual(
            requirement.values,
            {
                "consumer": "oaei-bioml-eval",
                "consumer_version": "0.2.0",
                "consumer_api": "coherence-provenance/1",
                "package_api": (0, 1),
                "adapter_protocol": 1,
                "model_schema": 1,
                "wire_major": 1,
                "minimum_wire_minor": 0,
                "required_encoded_view_schemas": {
                    "pyowl-core/structural-columns": 1
                },
            },
        )
        self.assertIs(require_compatible.call_args.args[0], ontology)

    def test_encoded_session_negotiation_fails_closed_without_both_envelopes(self):
        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        encoded = {
            "name": "native",
            "accelerated": True,
            "compiler_diagnostics": {"ingestion_path": "encoded-native"},
        }
        with self.assertRaisesRegex(
            NativeReasonerCompatibilityError,
            "lacks the public compiler_handoff",
        ):
            _validate_encoded_session_handoff(ontology, encoded)

        class Requirement:
            def __init__(self, **_values):
                pass

        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=mock.Mock(
                side_effect=RuntimeError("encoded view schema is unavailable")
            ),
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=adapters,
            ),
            self.assertRaisesRegex(
                NativeReasonerCompatibilityError,
                "core capability negotiation failed",
            ),
        ):
            _validate_encoded_session_handoff(
                ontology,
                {**encoded, "compiler_handoff": _compiler_handoff()},
            )

    def test_encoded_session_rejects_identity_version_or_acceleration_contradictions(self):
        class Requirement:
            def __init__(self, **_values):
                pass

        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        base = {
            "name": "native",
            "accelerated": True,
            "core_api_version": [0, 1],
            "core_adapter_protocol_version": 1,
            "core_model_schema_version": 1,
            "core_package_version": "0.1.0.dev0",
            "core_wire_format_version": [1, 1],
            "compiler_handoff": _compiler_handoff(),
            "compiler_diagnostics": {
                "ingestion_path": "encoded-native",
                "counters": _encoded_counters(),
            },
        }
        cases = (
            (
                {**base, "core_model_schema_version": 2},
                lambda view, _requirement: view,
                "core_model_schema_version",
            ),
            (
                {**base, "core_package_version": "0.1.1"},
                lambda view, _requirement: view,
                "core_package_version",
            ),
            (
                {**base, "core_api_version": [0, 2]},
                lambda view, _requirement: view,
                "core_api_version",
            ),
            (
                {**base, "core_wire_format_version": [1, 0]},
                lambda view, _requirement: view,
                "core_wire_format_version",
            ),
            (
                {**base, "accelerated": False},
                lambda view, _requirement: view,
                "native availability",
            ),
            (
                {**base, "name": "python"},
                lambda view, _requirement: view,
                "Python Reasoner backend",
            ),
            (
                base,
                lambda _view, _requirement: object(),
                "changed ontology identity",
            ),
        )
        for metadata, compatible, message in cases:
            adapters = types.SimpleNamespace(
                AdapterRequirement=Requirement,
                CoreContract=_CoreContract,
                require_compatible_view=compatible,
            )
            with (
                self.subTest(message=message),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                    return_value=adapters,
                ),
                self.assertRaisesRegex(NativeReasonerCompatibilityError, message),
            ):
                _validate_encoded_session_handoff(ontology, metadata)

        partial = dict(base)
        del partial["core_wire_format_version"]
        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=lambda view, _requirement: view,
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=adapters,
            ),
            self.assertRaisesRegex(
                NativeReasonerCompatibilityError,
                "incomplete public core contract",
            ),
        ):
            _validate_encoded_session_handoff(ontology, partial)

    def test_encoded_session_rejects_known_work_and_zero_copy_contradictions(self):
        class Requirement:
            def __init__(self, **_values):
                pass

        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=lambda view, _requirement: view,
        )
        base = {
            "name": "native",
            "accelerated": True,
            "compiler_handoff": _compiler_handoff(),
            "compiler_diagnostics": {
                "ingestion_path": "encoded-native",
                "counters": _encoded_counters(),
            },
        }
        cases = (
            ({"parser_calls": 1}, "forbidden work counters"),
            ({"materialized_scalar_rows": 2}, "forbidden work counters"),
            ({"structural_copy_bytes": 8}, "forbidden work counters"),
            ({"wire_encoder_calls": 1}, "forbidden work counters"),
            (
                {
                    "encoded_buffer_count": 11,
                    "encoded_zero_copy_buffers": 10,
                },
                "every encoded buffer zero-copy",
            ),
        )
        for counters, message in cases:
            with (
                self.subTest(counters=counters),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                    return_value=adapters,
                ),
                self.assertRaisesRegex(NativeReasonerCompatibilityError, message),
            ):
                _validate_encoded_session_handoff(
                    ontology,
                    {
                        **base,
                        "compiler_diagnostics": {
                            "ingestion_path": "encoded-native",
                            "counters": _encoded_counters(**counters),
                        },
                    },
                )

        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=adapters,
        ):
            _validate_encoded_session_handoff(ontology, base)

        incomplete_diagnostics = (
            {"ingestion_path": "encoded-native"},
            {
                "ingestion_path": "encoded-native",
                "counters": {"encoded_buffer_count": 11},
            },
        )
        for diagnostics in incomplete_diagnostics:
            with (
                self.subTest(diagnostics=diagnostics),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                    return_value=adapters,
                ),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    "mapping|incomplete",
                ),
            ):
                _validate_encoded_session_handoff(
                    ontology,
                    {**base, "compiler_diagnostics": diagnostics},
                )

        for counters, message in (
            (_encoded_counters(encoded_buffer_bytes=0), "no retained structural buffers"),
            (
                _encoded_counters(encoded_referenced_view_count=4),
                "referenced more views",
            ),
        ):
            with (
                self.subTest(message=message),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                    return_value=adapters,
                ),
                self.assertRaisesRegex(NativeReasonerCompatibilityError, message),
            ):
                _validate_encoded_session_handoff(
                    ontology,
                    {
                        **base,
                        "compiler_diagnostics": {
                            "ingestion_path": "encoded-native",
                            "counters": counters,
                        },
                    },
                )

    def test_encoded_session_accepts_bounded_sidecar_counters(self):
        class Requirement:
            def __init__(self, **_values):
                pass

        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=lambda view, _requirement: view,
        )
        metadata = {
            "name": "native",
            "accelerated": True,
            "compiler_handoff": _compiler_handoff(),
            "compiler_diagnostics": {
                "ingestion_path": "encoded-native",
                "counters": _encoded_counters(
                    encoded_detached_buffer_count=14,
                    encoded_indexed_buffer_count=2,
                    encoded_posting_bytes=128,
                    encoded_private_ir_bytes=256,
                    encoded_staging_copy_bytes=64,
                ),
            },
        }
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=adapters,
        ):
            _validate_encoded_session_handoff(ontology, metadata)

    def test_scalar_session_does_not_require_encoded_negotiation(self):
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module"
        ) as importer:
            _validate_encoded_session_handoff(
                object(),
                {
                    "compiler_diagnostics": {
                        "ingestion_path": "scalar-python",
                    }
                },
            )
        importer.assert_not_called()

    def test_public_compiler_diagnostics_are_bounded_and_canonical(self):
        session = types.SimpleNamespace(
            backend=_Backend(),
            diagnostics=lambda: {
                "ingestion_path": "encoded-native",
                "compiler_digest": "a" * 64,
                "compiler_cache_schema_version": 2,
                "ir_schema_version": 3,
                "native_abi_version": 4,
                "consumer_compile_seconds": 0.25,
                "encoded_view_publication_seconds": 0.125,
                "encoded_buffer_count": 11,
                "encoded_staging_copy_bytes": 0,
                "encoded_compiler_gil_released": True,
                "materialized_scalar_rows": 0,
                "private_arena_id": "/private/tmp/forbidden",
            },
        )

        metadata = _backend_metadata(session, "0.1.0.dev0")

        self.assertEqual(
            metadata["compiler_diagnostics"],
            {
                "ingestion_path": "encoded-native",
                "compiler_digest": "a" * 64,
                "compiler_cache_schema_version": 2,
                "ir_schema_version": 3,
                "native_abi_version": 4,
                "consumer_compile_seconds": 0.25,
                "encoded_view_publication_seconds": 0.125,
                "counters": {
                    "encoded_buffer_count": 11,
                    "encoded_compiler_gil_released": True,
                    "encoded_staging_copy_bytes": 0,
                    "materialized_scalar_rows": 0,
                },
            },
        )
        self.assertNotIn("private_arena_id", metadata["compiler_diagnostics"])

    def test_malformed_public_compiler_diagnostics_fail_closed(self):
        cases = (
            None,
            {"ingestion_path": "private-native"},
            {"ingestion_path": "encoded-native", "compiler_digest": "/private/tmp"},
            {"ingestion_path": "encoded-native", "encoded_buffer_count": True},
            {"ingestion_path": "encoded-native", "compiler_cache_schema_version": True},
            {"ingestion_path": "encoded-native", "ir_schema_version": 0},
            {"ingestion_path": "encoded-native", "native_abi_version": ""},
            {"ingestion_path": "encoded-native", "consumer_compile_seconds": True},
            {
                "ingestion_path": "encoded-native",
                "encoded_view_publication_seconds": float("nan"),
            },
            {
                "ingestion_path": "scalar-python",
                "encoded_view_publication_seconds": 0.25,
            },
            {
                "ingestion_path": "scalar-python",
                "encoded_validation_seconds": 0.25,
            },
            {
                "ingestion_path": "scalar-python",
                "encoded_buffer_count": 1,
            },
            {
                "ingestion_path": "encoded-native",
                "encoded_compiler_gil_released": 1,
            },
        )
        for diagnostics in cases:
            with (
                self.subTest(diagnostics=diagnostics),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    "diagnostic|ingestion_path|claimed",
                ),
            ):
                _backend_metadata(
                    types.SimpleNamespace(
                        backend=_Backend(),
                        diagnostics=lambda diagnostics=diagnostics: diagnostics,
                    ),
                    "0.1.0.dev0",
                )

    def test_exact_compiler_handoff_is_canonicalized_after_validation(self):
        advertised = _compiler_handoff()
        advertised["buffer_widths"] = dict(reversed(tuple(_ENCODED_BUFFER_WIDTHS.items())))
        backend = types.SimpleNamespace(compiler_handoff=advertised)
        metadata = _backend_metadata(
            types.SimpleNamespace(backend=backend),
            "0.1.0.dev0",
        )
        handoff = metadata["compiler_handoff"]
        self.assertEqual(handoff["descriptor_sha256"], advertised["descriptor_sha256"])
        self.assertEqual(
            list(handoff["buffer_widths"]),
            sorted(_ENCODED_BUFFER_WIDTHS),
        )

    def test_malformed_optional_backend_core_contract_fails_closed(self):
        cases = (
            ("core_package_version", ""),
            ("core_model_schema_version", True),
            ("core_adapter_protocol_version", 0),
            ("core_api_version", (0, True)),
            ("core_wire_format_version", [1, 1]),
        )
        for name, value in cases:
            with (
                self.subTest(name=name, value=value),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    name,
                ),
            ):
                _backend_metadata(
                    types.SimpleNamespace(backend=types.SimpleNamespace(**{name: value})),
                    "0.1.0.dev0",
                )

    def test_partial_or_non_mapping_compiler_handoff_fails_closed(self):
        invalid = _compiler_handoff()
        del invalid["descriptor_sha256"]
        for advertised in (invalid, None, (), object()):
            with (
                self.subTest(advertised=type(advertised).__name__),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    "compiler_handoff",
                ),
            ):
                _backend_metadata(
                    types.SimpleNamespace(
                        backend=types.SimpleNamespace(compiler_handoff=advertised)
                    ),
                    "0.1.0.dev0",
                )

    def test_extra_or_inexact_buffer_width_fails_closed(self):
        for name, value, remove in (
            ("extra_buffer", 1, False),
            ("root_ids", 8, False),
            ("root_ids", True, False),
            ("scalar_bytes", None, True),
        ):
            advertised = _compiler_handoff()
            if remove:
                del advertised["buffer_widths"][name]
            else:
                advertised["buffer_widths"][name] = value
            with (
                self.subTest(name=name, value=value, remove=remove),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    "buffer|width",
                ),
            ):
                _backend_metadata(
                    types.SimpleNamespace(
                        backend=types.SimpleNamespace(compiler_handoff=advertised)
                    ),
                    "0.1.0.dev0",
                )

    def test_descriptor_and_model_schema_drift_fail_closed(self):
        for field, value in (
            ("descriptor_sha256", "0" * 64),
            ("model_schema", 2),
            ("model_schema", True),
            ("schema_version", True),
        ):
            advertised = _compiler_handoff()
            advertised[field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(
                    NativeReasonerCompatibilityError,
                    field,
                ),
            ):
                _backend_metadata(
                    types.SimpleNamespace(
                        backend=types.SimpleNamespace(compiler_handoff=advertised)
                    ),
                    "0.1.0.dev0",
                )


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
            with (
                self.subTest(mode=mode),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners.named_class_iris",
                    return_value=(A, B, C, D),
                ),
            ):
                _HermiTSession.mode = mode
                result = self._classify()
                self.assertEqual(result.unsatisfiable, (A, B, C, D))
                self.assertTrue(result.inconsistent)

    def test_only_public_timeout_maps_to_fallback_signal(self):
        _HermiTSession.mode = "timeout"
        with self.assertRaises(HermiTTimeoutError):
            self._classify()
        self.assertTrue(_HermiTSession.disposed)

        _HermiTSession.mode = "consistent"
        retry = self._classify()
        self.assertEqual(retry.unsatisfiable, (A, B))
        self.assertTrue(_HermiTSession.disposed)

        _HermiTSession.mode = "failure"
        with self.assertRaisesRegex(RuntimeError, "semantic failure"):
            self._classify()

    def test_negotiation_failure_disposes_session_and_retry_is_clean(self):
        validator = mock.Mock(
            side_effect=(
                NativeReasonerCompatibilityError("incompatible encoded schema"),
                None,
            )
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners."
                "_validate_encoded_session_handoff",
                validator,
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=_pyhermit_module(),
            ),
        ):
            with self.assertRaisesRegex(
                NativeReasonerCompatibilityError,
                "incompatible encoded schema",
            ):
                HermiTReasoner().unsatisfiable_classes_view(
                    object(),
                    which="hermit",
                    timeout_s=2.5,
                )
            self.assertTrue(_HermiTSession.disposed)

            retry = HermiTReasoner().unsatisfiable_classes_view(
                object(),
                which="hermit",
                timeout_s=2.5,
            )
        self.assertEqual(retry.unsatisfiable, (A, B))
        self.assertTrue(_HermiTSession.disposed)
        self.assertEqual(validator.call_count, 2)


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
            result = ELKReasoner().unsatisfiable_classes_view(ontology, which="elk", timeout_s=None)
        self.assertIs(_ELKSession.last_ontology, ontology)
        self.assertEqual(result.unsatisfiable, (A, B))
        self.assertEqual(result.provenance["transport"]["mode"], "in-process-identity")
        self.assertTrue(_ELKSession.closed)

    def test_negotiation_failure_closes_session_and_retry_is_clean(self):
        validator = mock.Mock(
            side_effect=(
                NativeReasonerCompatibilityError("incompatible encoded schema"),
                None,
            )
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners."
                "_validate_encoded_session_handoff",
                validator,
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=_pyelk_module(),
            ),
        ):
            with self.assertRaisesRegex(
                NativeReasonerCompatibilityError,
                "incompatible encoded schema",
            ):
                ELKReasoner().unsatisfiable_classes_view(
                    object(),
                    which="elk",
                    timeout_s=None,
                )
            self.assertTrue(_ELKSession.closed)

            retry = ELKReasoner().unsatisfiable_classes_view(
                object(),
                which="elk",
                timeout_s=None,
            )
        self.assertEqual(retry.unsatisfiable, (A, B))
        self.assertTrue(_ELKSession.closed)
        self.assertEqual(validator.call_count, 2)

    def test_unbounded_call_records_public_compiler_diagnostics(self):
        class Requirement:
            def __init__(self, **_values):
                pass

        diagnostics = {
            "ingestion_path": "encoded-native",
            "compiler_digest": "c" * 64,
            "compiler_cache_schema_version": 2,
            "ir_schema_version": 3,
            "native_abi_version": "pyelk-native/1",
            **_encoded_counters(),
        }
        ontology = types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                adapter_protocol=1,
                model_schema=1,
            )
        )
        adapters = types.SimpleNamespace(
            AdapterRequirement=Requirement,
            CoreContract=_CoreContract,
            require_compatible_view=lambda view, _requirement: view,
        )
        modules = {
            "pyelk": _pyelk_module(),
            "pyowl_core.adapters": adapters,
        }
        with (
            mock.patch.object(
                _ELKSession,
                "diagnostics",
                return_value=diagnostics,
                create=True,
            ),
            mock.patch.object(_Backend, "name", "rust"),
            mock.patch.object(_Backend, "accelerated", True),
            mock.patch.object(
                _Backend,
                "compiler_handoff",
                _compiler_handoff(),
                create=True,
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                side_effect=lambda name: modules[name],
            ),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(
                ontology, which="elk", timeout_s=None
            )

        self.assertEqual(
            result.provenance["backend"]["compiler_diagnostics"],
            {
                "ingestion_path": "encoded-native",
                "compiler_digest": "c" * 64,
                "compiler_cache_schema_version": 2,
                "ir_schema_version": 3,
                "native_abi_version": "pyelk-native/1",
                "counters": dict(sorted(_encoded_counters().items())),
            },
        )

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
            True,
            0,
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
                return_value=object(),
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners._encode_core_wire",
                return_value=envelope,
            ) as encode,
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners._run_elk_worker",
                return_value=outcome,
            ) as worker,
        ):
            result = ELKReasoner().unsatisfiable_classes_view(ontology, which="elk", timeout_s=3.0)
        encode.assert_called_once_with(ontology)
        worker.assert_called_once_with(envelope, timeout_s=3.0)
        self.assertEqual(result.unsatisfiable, (A,))
        transport = result.provenance["transport"]
        self.assertTrue(transport["wire_verified"])
        self.assertTrue(transport["mmap_verified"])
        self.assertEqual(transport["owl_parse_count"], 0)

    def test_bounded_call_requires_the_frozen_core_wire_api(self):
        incomplete_core = types.ModuleType("pyowl_core")
        incomplete_core.WIRE_FORMAT_VERSION = (1, 0)
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
                return_value=object(),
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=incomplete_core,
            ),
            self.assertRaisesRegex(NativeReasonerCompatibilityError, "wire contract"),
        ):
            ELKReasoner().unsatisfiable_classes_view(object(), which="elk", timeout_s=1.0)

    def test_unverified_or_reparsed_worker_result_is_rejected(self):
        envelope = CoreWireEnvelope(b"wire", {}, (1, 0), "0.1.0.dev0")
        for wire_verified, mmap_verified, owl_parse_count in (
            (False, True, 0),
            (True, False, 0),
            (True, True, 1),
        ):
            outcome = ELKWorkerResult(
                (),
                False,
                {"package_version": "0.1.0.dev0"},
                {"complete": True, "reasons": []},
                wire_verified,
                mmap_verified,
                owl_parse_count,
            )
            with (
                self.subTest(
                    wire_verified=wire_verified,
                    mmap_verified=mmap_verified,
                    owl_parse_count=owl_parse_count,
                ),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners._load_pyelk_api",
                    return_value=object(),
                ),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners._encode_core_wire",
                    return_value=envelope,
                ),
                mock.patch(
                    "oaei_bioml_eval.coherence.native_reasoners._run_elk_worker",
                    return_value=outcome,
                ),
                self.assertRaisesRegex(NativeWorkerError, "verified core mmap"),
            ):
                ELKReasoner().unsatisfiable_classes_view(
                    object(), which="elk", timeout_s=1.0
                )

    def test_inconsistent_elk_result_expands_to_the_full_signature(self):
        _ELKSession.inconsistent = True
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.named_class_iris",
                return_value=(A, B, C, D),
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=_pyelk_module(),
            ),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(object(), which="elk", timeout_s=None)
        self.assertTrue(result.inconsistent)
        self.assertEqual(result.unsatisfiable, (A, B, C, D))

    def test_profile_limitations_are_preserved_in_provenance(self):
        _ELKSession.reasons = (_CompletenessIssue(),)
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=_pyelk_module(),
        ):
            result = ELKReasoner().unsatisfiable_classes_view(object(), which="elk", timeout_s=None)
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
        result = _run_elk_worker(self.envelope, timeout_s=5.0, entrypoint=_success_worker)
        self.assertEqual(result.unsatisfiable, (A, B))
        self.assertTrue(result.wire_verified)
        self.assertTrue(result.mmap_verified)
        self.assertEqual(result.owl_parse_count, 0)

    def test_timeout_terminates_worker_and_retry_is_clean(self):
        started = time.perf_counter()
        with self.assertRaises(ELKTimeoutError):
            _run_elk_worker(self.envelope, timeout_s=0.05, entrypoint=_sleep_worker)
        self.assertLess(time.perf_counter() - started, 2.0)
        retry = _run_elk_worker(
            self.envelope,
            timeout_s=5.0,
            entrypoint=_success_worker,
        )
        self.assertEqual(retry.unsatisfiable, (A, B))
        self.assertTrue(retry.wire_verified)
        self.assertTrue(retry.mmap_verified)
        self.assertEqual(retry.owl_parse_count, 0)

    def test_worker_exit_is_error_not_a_semantic_value(self):
        with self.assertRaises(NativeWorkerError):
            _run_elk_worker(self.envelope, timeout_s=5.0, entrypoint=_silent_worker)

    def test_corrupt_wire_is_a_typed_error_and_closes_the_connection(self):
        core = types.ModuleType("pyowl_core")

        def open_snapshot(path, *, mmap, verify):
            del path, mmap, verify
            raise ValueError("wire checksum mismatch")

        core.open_snapshot = open_snapshot

        class Connection:
            payload = b""
            closed = False

            def send_bytes(self, payload):
                self.payload = payload

            def close(self):
                self.closed = True

        connection = Connection()
        with mock.patch(
            "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
            return_value=core,
        ):
            _elk_worker_entry(
                connection,
                "/tmp/corrupt.pyocore",
                {"logical_fingerprint": "a" * 64},
            )

        self.assertTrue(connection.closed)
        with self.assertRaisesRegex(
            NativeWorkerError,
            "ValueError: wire checksum mismatch",
        ):
            _decode_worker_response(connection.payload, 0)

    def test_worker_retains_verified_mmap_owner_through_classification(self):
        events = []

        class Fingerprint:
            hex = "a" * 64

        class MappedOntology:
            capabilities = types.SimpleNamespace(
                features=frozenset({"wire-verified", "mmap-snapshot"})
            )
            structural_fingerprint = Fingerprint()
            logical_fingerprint = Fingerprint()
            signature_fingerprint = Fingerprint()

            def close(self):
                events.append("close")

        mapped = MappedOntology()
        core = types.ModuleType("pyowl_core")

        def open_snapshot(path, *, mmap, verify):
            self.assertEqual(path, "/tmp/test.pyocore")
            self.assertTrue(mmap)
            self.assertTrue(verify)
            events.append("open")
            return mapped

        core.open_snapshot = open_snapshot

        class Connection:
            payload = b""
            closed = False

            def send_bytes(self, payload):
                self.payload = payload

            def close(self):
                self.closed = True

        connection = Connection()

        def classify(ontology):
            self.assertIs(ontology, mapped)
            self.assertEqual(events, ["open"])
            events.append("classify")
            return {
                "unsatisfiable": [],
                "inconsistent": False,
                "reasoner": {"package_version": "0.1.0.dev0"},
                "profile": {"complete": True, "reasons": []},
            }

        expected = {
            "structural_fingerprint": "a" * 64,
            "logical_fingerprint": "a" * 64,
            "signature_fingerprint": "a" * 64,
        }
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners._classify_elk_identity",
                side_effect=classify,
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.native_reasoners.importlib.import_module",
                return_value=core,
            ),
        ):
            _elk_worker_entry(connection, "/tmp/test.pyocore", expected)

        response = json.loads(connection.payload)
        self.assertTrue(response["ok"])
        self.assertTrue(response["result"]["mmap_verified"])
        self.assertEqual(response["result"]["owl_parse_count"], 0)
        self.assertEqual(events, ["open", "classify", "close"])
        self.assertTrue(connection.closed)

    def test_envelope_records_the_emitted_wire_minor_not_the_capability_max(self):
        core = types.ModuleType("pyowl_core")
        core.__version__ = "0.1.0.dev0"
        core.WIRE_FORMAT_VERSION = (1, 1)

        def encode_snapshot(ontology):
            del ontology
            return b"PYOCORE\0\x01\x00\x00\x00"

        def open_snapshot(path, *, mmap, verify):
            del path, mmap, verify
            return object()

        core.encode_snapshot = encode_snapshot
        core.open_snapshot = open_snapshot

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
        with (
            mock.patch("oaei_bioml_eval.coherence.report.load_reasoner", return_value=router),
            mock.patch(
                "oaei_bioml_eval.coherence.report.compose_alignment_views",
                side_effect=_Composite,
            ),
            mock.patch(
                "oaei_bioml_eval.coherence.report.named_class_iris",
                return_value=(A, B),
            ),
        ):
            return score_global_coherence([(A, B)], object(), object(), timeout_s=1.0)

    def test_timeout_only_falls_back_on_same_composite(self):
        hermit = _RecordingAdapter(error=HermiTTimeoutError("timeout", elapsed_seconds=0.5))
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
        hermit = _RecordingAdapter(result=UnsatResult((A, B), "hermit", 0.01, inconsistent=True))
        report = self._score(hermit, _RecordingAdapter())
        self.assertEqual(report["global_coherence"], 1.0)
        self.assertTrue(report["inconsistent"])
        self.assertEqual(report["provenance"]["schema"], "coherence-provenance/1")
        self.assertTrue(report["provenance"]["reasoner"]["inconsistent"])

    def test_provenance_is_deterministic_and_contains_no_local_identity(self):
        bridge = analyze_correspondences([(A, B), (A, B, "equiv"), (A, A)], invalid_dropped_count=2)
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

    def test_compiler_handoff_records_public_core_shape_without_path_claims(self):
        bridge = analyze_correspondences([])
        result = UnsatResult((), "hermit", 0.1)
        cases = (
            ((), {}, "python", None, "direct", "python"),
            (
                ("wire-verified",),
                {"pyowl-core/structural-columns": 1},
                "python",
                None,
                "direct",
                "decoded",
            ),
            (
                ("wire-verified", "mmap-snapshot"),
                {},
                "python",
                None,
                "direct",
                "mmap",
            ),
            (
                (),
                {},
                "mixed",
                types.SimpleNamespace(kind=types.SimpleNamespace(value="composite")),
                "composite",
                "mixed",
            ),
        )
        for features, schemas, backend, context, owner_kind, storage_kind in cases:
            with self.subTest(owner_kind=owner_kind, storage_kind=storage_kind):
                ontology = types.SimpleNamespace(
                    capabilities=types.SimpleNamespace(
                        features=frozenset(features),
                        encoded_view_schemas=schemas,
                        backend=backend,
                    ),
                    structural_context=context,
                )
                provenance = build_coherence_provenance(
                    ontology,
                    bridge,
                    (),
                    result,
                    requested_reasoner="hermit",
                    timeout_s=None,
                )
                handoff = provenance["compiler_handoff"]
                self.assertEqual(handoff["core_encoded_view_schemas"], schemas)
                self.assertEqual(handoff["owner_kind"], owner_kind)
                self.assertEqual(handoff["storage_kind"], storage_kind)
                self.assertEqual(handoff["requested_reasoner"], "hermit")
                self.assertEqual(handoff["selected_reasoner"], "hermit")
                self.assertNotIn("compilation_path", handoff)
                self.assertNotIn("counters", handoff)

    def test_compiler_handoff_capability_absence_is_an_empty_noop(self):
        provenance = build_coherence_provenance(
            object(),
            analyze_correspondences([]),
            (),
            UnsatResult((), "hermit", 0.1),
            requested_reasoner="hermit",
            timeout_s=None,
        )
        self.assertEqual(
            provenance["compiler_handoff"],
            {
                "core_encoded_view_schemas": {},
                "owner_kind": None,
                "storage_kind": None,
                "requested_reasoner": "hermit",
                "selected_reasoner": "hermit",
            },
        )

    def test_compiler_handoff_records_selected_public_reasoner_diagnostics(self):
        provenance = build_coherence_provenance(
            object(),
            analyze_correspondences([]),
            (),
            UnsatResult(
                (),
                "elk",
                0.1,
                provenance={
                    "package_version": "0.1.0.dev0",
                    "backend": {
                        "name": "rust",
                        "implementation_version": "pyelk-native-test",
                        "ir_schema_version": 1,
                        "compiler_diagnostics": {
                            "ingestion_path": "encoded-native",
                            "compiler_digest": "b" * 64,
                            "compiler_cache_schema_version": 2,
                            "ir_schema_version": 3,
                            "native_abi_version": 4,
                            "consumer_compile_seconds": 0.25,
                            "encoded_view_publication_seconds": 0.125,
                            "counters": {
                                "encoded_buffer_count": 11,
                                "encoded_staging_copy_bytes": 0,
                                "materialized_scalar_rows": 0,
                            },
                        },
                    },
                },
            ),
            requested_reasoner="hermit",
            timeout_s=1.0,
        )

        handoff = provenance["compiler_handoff"]
        self.assertEqual(handoff["requested_reasoner"], "hermit")
        self.assertEqual(handoff["selected_reasoner"], "elk")
        self.assertEqual(handoff["selected_backend"], "rust")
        self.assertEqual(handoff["implementation_version"], "pyelk-native-test")
        self.assertEqual(handoff["reasoner_ir_schema_version"], 1)
        self.assertEqual(handoff["ingestion_path"], "encoded-native")
        self.assertEqual(handoff["compiler_digest"], "b" * 64)
        self.assertEqual(handoff["compiler_cache_schema_version"], 2)
        self.assertEqual(handoff["ir_schema_version"], 3)
        self.assertEqual(handoff["native_abi_version"], 4)
        self.assertEqual(handoff["consumer_compile_seconds"], 0.25)
        self.assertEqual(handoff["encoded_view_publication_seconds"], 0.125)
        self.assertEqual(
            handoff["counters"],
            {
                "encoded_buffer_count": 11,
                "encoded_staging_copy_bytes": 0,
                "materialized_scalar_rows": 0,
            },
        )

    def test_compiler_handoff_rejects_private_reasoner_diagnostic_fields(self):
        with self.assertRaisesRegex(TypeError, "unsupported fields"):
            build_coherence_provenance(
                object(),
                analyze_correspondences([]),
                (),
                UnsatResult(
                    (),
                    "elk",
                    0.1,
                    provenance={
                        "backend": {
                            "compiler_diagnostics": {
                                "ingestion_path": "encoded-native",
                                "private_arena_id": "/private/tmp/forbidden",
                            }
                        }
                    },
                ),
                requested_reasoner="elk",
                timeout_s=None,
            )

    def test_compiler_handoff_rejects_malformed_public_schema_diagnostics(self):
        for name, value in (
            ("compiler_cache_schema_version", True),
            ("ir_schema_version", 0),
            ("native_abi_version", ""),
            ("consumer_compile_seconds", True),
            ("encoded_view_publication_seconds", float("inf")),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(TypeError, "must be"):
                build_coherence_provenance(
                    object(),
                    analyze_correspondences([]),
                    (),
                    UnsatResult(
                        (),
                        "elk",
                        0.1,
                        provenance={
                            "backend": {
                                "compiler_diagnostics": {
                                    "ingestion_path": "encoded-native",
                                    name: value,
                                }
                            }
                        },
                    ),
                    requested_reasoner="elk",
                    timeout_s=None,
                )

        for name, value, message in (
            ("encoded_view_publication_seconds", 0.25, "encoded-only timing"),
            ("encoded_validation_seconds", 0.25, "encoded-only timing"),
            ("encoded_buffer_count", 1, "nonzero encoded resources"),
            ("encoded_compiler_gil_released", True, "nonzero encoded resources"),
        ):
            diagnostic = (
                {"counters": {name: value}}
                if name.startswith("encoded_") and not name.endswith("_seconds")
                else {name: value}
            )
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                build_coherence_provenance(
                    object(),
                    analyze_correspondences([]),
                    (),
                    UnsatResult(
                        (),
                        "elk",
                        0.1,
                        provenance={
                            "backend": {
                                "compiler_diagnostics": {
                                    "ingestion_path": "scalar-python",
                                    **diagnostic,
                                }
                            }
                        },
                    ),
                    requested_reasoner="elk",
                    timeout_s=None,
                )

    def test_malformed_core_schema_advertisement_is_not_silently_normalized(self):
        bridge = analyze_correspondences([])
        for schemas in (
            {"pyowl-core/structural-columns": True},
            {"pyowl-core/structural-columns": 0},
            {1: 1},
            (),
        ):
            ontology = types.SimpleNamespace(
                capabilities=types.SimpleNamespace(
                    features=frozenset(),
                    encoded_view_schemas=schemas,
                    backend="python",
                )
            )
            with (
                self.subTest(schemas=schemas),
                self.assertRaisesRegex(TypeError, "encoded"),
            ):
                build_coherence_provenance(
                    ontology,
                    bridge,
                    (),
                    UnsatResult((), "hermit", 0.1),
                    requested_reasoner="hermit",
                    timeout_s=None,
                )


class TestDeferredRealDataComparison(unittest.TestCase):
    def test_native_comparator_scores_the_exact_hash_bound_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.owl"
            target = root / "target.owl"
            alignment = root / "alignment.tsv"
            source.write_bytes(b"source")
            target.write_bytes(b"target")
            alignment.write_bytes(b"SrcEntity\tTgtEntity\nurn:source\turn:target\n")
            expected_report = {
                "reasoner_used": "elk",
                "union_class_count": 2,
                "asserted_correspondences": 1,
                "unsatisfiable_count": 1,
                "provenance": {
                    "schema": "coherence-provenance/1",
                    "result": {"numerator_sha256": "a" * 64},
                },
            }
            pinned_hashes = {
                "source": native_compare.sha256_file(source),
                "target": native_compare.sha256_file(target),
                "alignment": native_compare.sha256_file(alignment),
            }
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "schema": "robot-oracle-ncit-doid/1",
                        "inputs": {
                            "source": {"sha256": pinned_hashes["source"]},
                            "target": {"sha256": pinned_hashes["target"]},
                            "named_class_count": 2,
                        },
                        "bridge": {
                            "sha256": pinned_hashes["alignment"],
                            "mapping_count": 1,
                        },
                        "runs": {
                            "elk": {
                                "unsatisfiable_count": 1,
                                "unsatisfiable_sha256": "a" * 64,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            baseline_sha256 = native_compare.sha256_file(baseline)

            source_view = object()
            target_view = object()
            captured_ontologies: list[bytes] = []

            def capture_pair(source_payload, target_payload, **kwargs):
                del kwargs
                captured_ontologies.extend((source_payload, target_payload))
                source.write_bytes(b"changed source")
                target.write_bytes(b"changed target")
                alignment.write_bytes(b"changed alignment")
                return source_view, target_view

            with (
                mock.patch.object(
                    native_compare,
                    "score_reference_coherence",
                    return_value=expected_report,
                ) as score,
                mock.patch.object(
                    native_compare,
                    "coerce_ontology_pair_once",
                    side_effect=capture_pair,
                ),
                mock.patch("builtins.print") as output,
            ):
                result = native_compare.main(
                    [
                        "--source",
                        str(source),
                        "--target",
                        str(target),
                        "--alignment",
                        str(alignment),
                        "--baseline",
                        str(baseline),
                        "--reasoner",
                        "elk",
                        "--allow-python",
                        "--timeout",
                        "60",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertEqual(captured_ontologies, [b"source", b"target"])
        self.assertEqual(
            score.call_args.args,
            ([("urn:source", "urn:target", "=")], source_view, target_view),
        )
        kwargs = score.call_args.kwargs
        self.assertNotIn("backend", kwargs)
        self.assertEqual(kwargs["reasoner"], "elk")
        self.assertEqual(kwargs["timeout_s"], 60.0)
        comparison = json.loads(output.call_args.args[0])
        self.assertEqual(comparison["schema"], "oaei-bioml-eval.native-comparison/2")
        self.assertEqual(comparison["inputs"]["source"]["sha256"], pinned_hashes["source"])
        self.assertEqual(comparison["baseline"]["sha256"], baseline_sha256)
        self.assertFalse(comparison["runtime"]["accelerated_required"])
        self.assertGreaterEqual(
            comparison["execution"]["driver_pre_serialization_elapsed_seconds"], 0.0
        )

    def test_complete_comparator_worker_returns_only_its_small_result(self):
        request = native_compare.ComparatorRequest(
            source=Path("source.owl"),
            target=Path("target.owl"),
            alignment=Path("alignment.tsv"),
            baseline=Path("baseline.json"),
            reasoner="elk",
            reasoner_timeout_s=None,
        )
        result = native_compare.run_bounded_comparison(
            request,
            overall_timeout_s=5.0,
            entrypoint=_success_compare_worker,
        )
        self.assertEqual(result, {"agreement": True, "reasoner": "elk"})

    def test_complete_comparator_timeout_terminates_preprocessing_worker(self):
        request = native_compare.ComparatorRequest(
            source=Path("source.owl"),
            target=Path("target.owl"),
            alignment=Path("alignment.tsv"),
            baseline=Path("baseline.json"),
            reasoner="elk",
            reasoner_timeout_s=None,
        )
        started = time.perf_counter()
        with self.assertRaises(native_compare.ComparatorTimeoutError):
            native_compare.run_bounded_comparison(
                request,
                overall_timeout_s=0.05,
                entrypoint=_sleep_compare_worker,
            )
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_complete_comparator_rejects_a_nested_reasoner_worker(self):
        request = native_compare.ComparatorRequest(
            source=Path("source.owl"),
            target=Path("target.owl"),
            alignment=Path("alignment.tsv"),
            baseline=Path("baseline.json"),
            reasoner="elk",
            reasoner_timeout_s=60.0,
        )
        with self.assertRaisesRegex(ValueError, "requires --timeout none"):
            native_compare.run_bounded_comparison(
                request,
                overall_timeout_s=90.0,
                entrypoint=_success_compare_worker,
            )

    def test_native_release_gate_rejects_fallback_and_hashes_the_extension(self):
        fallback = {
            "provenance": {"reasoner": {"backend": {"name": "python", "accelerated": False}}}
        }
        with self.assertRaisesRegex(RuntimeError, "requires an accelerated native backend"):
            native_compare._runtime_evidence(
                fallback,
                reasoner="elk",
                require_accelerated=True,
            )

        with tempfile.TemporaryDirectory() as directory:
            extension = Path(directory) / "_native.abi3.so"
            extension.write_bytes(b"current native extension")
            accelerated = {
                "provenance": {
                    "reasoner": {
                        "backend": {
                            "name": "rust",
                            "accelerated": True,
                            "native_available": True,
                            "effective_workers": 12,
                        }
                    }
                }
            }
            with mock.patch.object(
                native_compare.importlib.util,
                "find_spec",
                return_value=types.SimpleNamespace(origin=str(extension)),
            ):
                evidence = native_compare._runtime_evidence(
                    accelerated,
                    reasoner="elk",
                    require_accelerated=True,
                )
        self.assertEqual(
            evidence["native_artifact"]["sha256"],
            hashlib.sha256(b"current native extension").hexdigest(),
        )
        self.assertEqual(evidence["backend"]["effective_workers"], 12)

    def test_main_routes_explicit_overall_timeout_to_single_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.owl"
            target = root / "target.owl"
            alignment = root / "alignment.tsv"
            source.write_bytes(b"source")
            target.write_bytes(b"target")
            alignment.write_bytes(b"alignment")
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "schema": "robot-oracle-ncit-doid/1",
                        "inputs": {
                            "source": {"sha256": native_compare.sha256_file(source)},
                            "target": {"sha256": native_compare.sha256_file(target)},
                            "named_class_count": 2,
                        },
                        "bridge": {
                            "sha256": native_compare.sha256_file(alignment),
                            "mapping_count": 1,
                        },
                        "runs": {
                            "elk": {
                                "unsatisfiable_count": 1,
                                "unsatisfiable_sha256": "a" * 64,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            expected = {"agreement": True}
            evidence_path = root / "comparison.json"
            with (
                mock.patch.object(
                    native_compare,
                    "run_bounded_comparison",
                    return_value=expected,
                ) as bounded,
                mock.patch("builtins.print"),
            ):
                result = native_compare.main(
                    [
                        "--source",
                        str(source),
                        "--target",
                        str(target),
                        "--alignment",
                        str(alignment),
                        "--baseline",
                        str(baseline),
                        "--output",
                        str(evidence_path),
                        "--reasoner",
                        "elk",
                        "--timeout",
                        "none",
                        "--overall-timeout",
                        "90",
                    ]
                )
            persisted = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        request = bounded.call_args.args[0]
        self.assertIsNone(request.reasoner_timeout_s)
        self.assertTrue(request.require_accelerated)
        self.assertEqual(bounded.call_args.kwargs["overall_timeout_s"], 90.0)
        self.assertTrue(persisted["agreement"])
        self.assertEqual(persisted["execution"]["overall_timeout_seconds"], 90.0)

    def test_public_ncit_doid_comparator_checks_all_frozen_semantics(self):
        baseline = json.loads(
            (Path(__file__).parent / "baselines" / "robot-1.9.10-ncit-doid-train.json").read_text(
                encoding="utf-8"
            )
        )
        expected = baseline["runs"]["elk"]
        report = {
            "reasoner_used": "elk",
            "union_class_count": baseline["inputs"]["named_class_count"],
            "asserted_correspondences": baseline["bridge"]["mapping_count"],
            "unsatisfiable_count": expected["unsatisfiable_count"],
            "provenance": {
                "schema": "coherence-provenance/1",
                "result": {"numerator_sha256": expected["unsatisfiable_sha256"]},
            },
        }
        input_evidence = {
            "source": baseline["inputs"]["source"],
            "target": baseline["inputs"]["target"],
            "alignment": {"sha256": baseline["bridge"]["sha256"]},
        }
        comparison = compare_report(
            report,
            baseline,
            reasoner="elk",
            input_evidence=input_evidence,
        )
        self.assertTrue(comparison["agreement"])
        changed = dict(report)
        changed["unsatisfiable_count"] = expected["unsatisfiable_count"] + 1
        with self.assertRaisesRegex(RuntimeError, "differs"):
            compare_report(
                changed,
                baseline,
                reasoner="elk",
                input_evidence=input_evidence,
            )

    def test_small_fixture_comparator_covers_every_pinned_case_and_backend(self):
        baseline = json.loads(
            (Path(__file__).parent / "baselines" / "robot-1.9.10.json").read_text(encoding="utf-8")
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
                            "result": {"numerator_sha256": sorted_line_sha256(unsatisfiable)}
                        },
                    }
                    compare_case(report, expected, reasoner=reasoner)


@unittest.skipUnless(_pyowl_core is not None, "pyowl-core not installed")
class TestConcreteCoreProvenance(unittest.TestCase):
    def test_advertised_core_contract_negotiates_by_identity(self):
        assert _pyowl_core is not None
        source = _pyowl_core.coerce_snapshot(
            f"Ontology(Declaration(Class(<{A}>)))".encode(),
            document_iri="urn:document:source",
        )
        target = _pyowl_core.coerce_snapshot(
            f"Ontology(Declaration(Class(<{B}>)))".encode(),
            document_iri="urn:document:target",
        )
        composite = compose_alignment_views(
            source,
            target,
            analyze_correspondences([(A, B)]).correspondences,
        )
        backend = types.SimpleNamespace(
            name="native",
            accelerated=True,
            core_package_version=_pyowl_core.__version__,
            core_api_version=_pyowl_core.API_VERSION,
            core_model_schema_version=_pyowl_core.MODEL_SCHEMA_VERSION,
            core_wire_format_version=_pyowl_core.WIRE_FORMAT_VERSION,
            core_adapter_protocol_version=_pyowl_core.ADAPTER_PROTOCOL_VERSION,
            compiler_handoff=_compiler_handoff(),
        )
        metadata = _backend_metadata(
            types.SimpleNamespace(
                backend=backend,
                diagnostics=lambda: {
                    "ingestion_path": "encoded-native",
                    **_encoded_counters(),
                },
            ),
            "0.1.0.dev0",
        )

        _validate_encoded_session_handoff(composite, metadata)

        self.assertEqual(metadata["core_api_version"], list(_pyowl_core.API_VERSION))
        self.assertEqual(
            metadata["core_wire_format_version"],
            list(_pyowl_core.WIRE_FORMAT_VERSION),
        )

    def test_composite_manifest_and_fingerprints_are_public_and_stable(self):
        assert _pyowl_core is not None
        source = _pyowl_core.coerce_snapshot(
            (
                f"Ontology(<urn:source> Declaration(Class(<{A}>)) Declaration(Class(<{C}>)))"
            ).encode(),
            document_iri="urn:document:source",
        )
        target = _pyowl_core.coerce_snapshot(
            (
                f"Ontology(<urn:target> Declaration(Class(<{B}>)) Declaration(Class(<{D}>)))"
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
        self.assertEqual(core["fingerprints"]["logical"], composite.logical_fingerprint.hex)
        self.assertEqual([item["role"] for item in core["roles"]], ["source", "target"])
        self.assertEqual(len(core["documents"]), 2)
        handoff = provenance["compiler_handoff"]
        self.assertEqual(
            handoff["core_encoded_view_schemas"],
            dict(composite.capabilities.encoded_view_schemas),
        )
        self.assertEqual(handoff["owner_kind"], "composite")
        self.assertEqual(handoff["storage_kind"], composite.capabilities.backend)
        self.assertNotIn("compilation_path", handoff)
        encoded = canonical_provenance_json(provenance)
        self.assertNotIn("urn:document:source", encoded)
        self.assertNotIn("urn:document:target", encoded)


if __name__ == "__main__":
    unittest.main()
