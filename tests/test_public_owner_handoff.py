"""Executable public-view parity for the OAEI coherence/reasoner boundary."""

from __future__ import annotations

import importlib.util
import unittest
from contextlib import ExitStack
from typing import Any
from unittest import mock

from oaei_bioml_eval.coherence.native_reasoners import (
    CoreWireEnvelope,
    ELKReasoner,
    ELKWorkerResult,
    HermiTTimeoutError,
    NativeReasoner,
    _validate_encoded_session_handoff,
)
from oaei_bioml_eval.coherence.reasoner import CoherenceReasoner, UnsatResult
from oaei_bioml_eval.coherence.report import score_reference_coherence

_HAS_CORE = importlib.util.find_spec("pyowl_core") is not None

A = "http://ex.org/A"
B = "http://ex.org/B"


def _compiler_handoff() -> dict[str, object]:
    return {
        "buffer_widths": {
            "field_kinds": 1,
            "field_lengths": 8,
            "field_values": 8,
            "item_kinds": 1,
            "item_lengths": 8,
            "item_values": 8,
            "node_field_offsets": 8,
            "node_tags": 2,
            "root_ids": 4,
            "root_kinds": 1,
            "scalar_bytes": 1,
        },
        "descriptor_sha256": (
            "9ad29db6a7e616f65cea2957bc5ba8d1f9b99ef0eb1fe1432c09be25786267b5"
        ),
        "model_schema": 1,
        "schema_name": "pyowl-core/structural-columns",
        "schema_version": 1,
    }


def _zero_work_counters() -> dict[str, int | bool]:
    return {
        "base_flattening_bytes": 0,
        "encoded_buffer_bytes": 4096,
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


def _backend_metadata(*, include_core_contract: bool) -> dict[str, object]:
    metadata: dict[str, object] = {
        "name": "native" if include_core_contract else "rust",
        "accelerated": True,
        "native_available": True,
        "compiler_handoff": _compiler_handoff(),
        "compiler_diagnostics": {
            "ingestion_path": "encoded-native",
            "counters": _zero_work_counters(),
        },
    }
    if include_core_contract:
        import pyowl_core

        metadata.update(
            {
                "core_adapter_protocol_version": pyowl_core.ADAPTER_PROTOCOL_VERSION,
                "core_api_version": list(pyowl_core.API_VERSION),
                "core_model_schema_version": pyowl_core.MODEL_SCHEMA_VERSION,
                "core_package_version": pyowl_core.__version__,
                "core_wire_format_version": list(pyowl_core.WIRE_FORMAT_VERSION),
            }
        )
    return metadata


class _AttestingAdapter(CoherenceReasoner):
    def __init__(self, *, reasoner: str, timeout: bool) -> None:
        self.reasoner = reasoner
        self.timeout = timeout
        self.calls: list[tuple[object, str, float | None]] = []

    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        self.calls.append((ontology, which, timeout_s))
        backend = _backend_metadata(include_core_contract=self.reasoner == "hermit")
        _validate_encoded_session_handoff(ontology, backend)
        provenance = {
            "schema": "native-reasoner-provenance/1",
            "package": "pyHermiT" if self.reasoner == "hermit" else "pyELK",
            "package_version": "0.1.0.dev0",
            "backend": backend,
            "transport": {"mode": "in-process-identity"},
            "profile": {"complete": True, "reasons": []},
        }
        if self.timeout:
            raise HermiTTimeoutError(
                "cooperative timeout",
                elapsed_seconds=0.01,
                attempt={
                    "status": "timeout",
                    "backend": backend,
                    "transport": {"mode": "in-process-identity"},
                },
            )
        return UnsatResult(
            (A,),
            self.reasoner,
            0.01,
            provenance=provenance,
        )


@unittest.skipUnless(_HAS_CORE, "pyowl-core is unavailable")
class TestPublicOwnerHandoff(unittest.TestCase):
    def _forbid_in_process_conversion(self, pyowl_core: Any) -> ExitStack:
        forbidden = AssertionError("in-process OAEI handoff crossed a conversion boundary")
        patches = [
            mock.patch.object(pyowl_core, name, side_effect=forbidden)
            for name in (
                "coerce_snapshot",
                "decode_snapshot",
                "encode_snapshot",
                "load_snapshot",
                "open_snapshot",
                "parse_document",
            )
        ]
        stack = ExitStack()
        for patch in patches:
            stack.enter_context(patch)
        stack.enter_context(
            mock.patch.object(
                pyowl_core.OntologyComposite,
                "materialize",
                side_effect=AssertionError("composite materialization attempted"),
            )
        )
        stack.enter_context(
            mock.patch.object(
                pyowl_core.OntologyComposite,
                "view",
                side_effect=AssertionError("OAEI inspected encoded structural buffers"),
            )
        )
        stack.enter_context(
            mock.patch.object(
                pyowl_core.OntologyOverlay,
                "materialize",
                side_effect=AssertionError("overlay base flattening attempted"),
            )
        )
        return stack

    def test_direct_mmap_overlay_and_composite_views_keep_one_identity_and_ledger(
        self,
    ) -> None:
        import pyowl_core

        from tools import installed_native_smoke

        source_bytes, target_bytes = installed_native_smoke.FORMAT_FIXTURES["functional"]
        source = pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:public-owner:source",
        )
        target = pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:public-owner:target",
        )
        observed_semantics: set[tuple[object, ...]] = set()
        with installed_native_smoke._owner_pairs(source, target) as pairs:
            for owner_name in ("direct", "mmap", "overlay", "composite"):
                owned_source, owned_target = pairs[owner_name]
                hermit = _AttestingAdapter(reasoner="hermit", timeout=True)
                elk = _AttestingAdapter(reasoner="elk", timeout=False)
                router = NativeReasoner(hermit=hermit, elk=elk)
                with (
                    self.subTest(owner=owner_name),
                    self._forbid_in_process_conversion(pyowl_core),
                    mock.patch(
                        "oaei_bioml_eval.coherence.report.load_reasoner",
                        return_value=router,
                    ),
                ):
                    report = score_reference_coherence(
                        ((A, B, "="),),
                        owned_source,
                        owned_target,
                        reasoner="hermit",
                        timeout_s=None,
                    )

                self.assertEqual(len(hermit.calls), 1)
                self.assertEqual(len(elk.calls), 1)
                merged = hermit.calls[0][0]
                self.assertIs(merged, elk.calls[0][0])
                self.assertTrue(
                    installed_native_smoke._retains_owner_leaves(
                        merged,
                        owned_source,
                        owned_target,
                    )
                )
                self.assertEqual(hermit.calls[0][1:], ("hermit", None))
                self.assertEqual(elk.calls[0][1:], ("elk", None))
                provenance = report["provenance"]
                self.assertEqual(
                    provenance["reasoner"]["fallback_reason"],
                    "hermit-timeout",
                )
                handoff = provenance["compiler_handoff"]
                self.assertEqual(handoff["ingestion_path"], "encoded-native")
                self.assertEqual(handoff["counters"], _zero_work_counters())
                observed_semantics.add(
                    (
                        report["global_coherence"],
                        report["union_class_count"],
                        report["unsatisfiable_count"],
                        provenance["result"]["denominator_sha256"],
                        provenance["result"]["numerator_sha256"],
                    )
                )
        self.assertEqual(len(observed_semantics), 1)

    def test_timeout_fallback_crosses_one_verified_worker_boundary(self) -> None:
        import pyowl_core

        from tools import installed_native_smoke

        source_bytes, target_bytes = installed_native_smoke.FORMAT_FIXTURES["functional"]
        source = pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:bounded-fallback:source",
        )
        target = pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:bounded-fallback:target",
        )
        hermit = _AttestingAdapter(reasoner="hermit", timeout=True)
        router = NativeReasoner(hermit=hermit, elk=ELKReasoner())
        envelope = CoreWireEnvelope(
            b"PYOCORE\0\x01\x00\x00\x00",
            {"logical_fingerprint": "a" * 64},
            (1, 0),
            pyowl_core.__version__,
        )
        worker_result = ELKWorkerResult(
            (A,),
            False,
            {
                "name": "rust",
                "package_version": "0.1.0.dev0",
                "compiler_handoff": _compiler_handoff(),
                "compiler_diagnostics": {
                    "ingestion_path": "encoded-native",
                    "counters": _zero_work_counters(),
                },
            },
            {"complete": True, "reasons": []},
            True,
            True,
            0,
        )
        with (
            mock.patch(
                "oaei_bioml_eval.coherence.report.load_reasoner",
                return_value=router,
            ),
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
                return_value=worker_result,
            ) as worker,
            mock.patch.object(
                pyowl_core.OntologyComposite,
                "materialize",
                side_effect=AssertionError("composite materialization attempted"),
            ),
        ):
            report = score_reference_coherence(
                ((A, B, "="),),
                source,
                target,
                reasoner="hermit",
                timeout_s=2.0,
            )

        merged = hermit.calls[0][0]
        encode.assert_called_once_with(merged)
        worker.assert_called_once_with(envelope, timeout_s=2.0)
        transport = report["provenance"]["reasoner"]["transport"]
        self.assertEqual(transport["mode"], "core-wire-worker")
        self.assertTrue(transport["wire_verified"])
        self.assertTrue(transport["mmap_verified"])
        self.assertEqual(transport["owl_parse_count"], 0)
        self.assertEqual(
            report["provenance"]["reasoner"]["fallback_reason"],
            "hermit-timeout",
        )


if __name__ == "__main__":
    unittest.main()
