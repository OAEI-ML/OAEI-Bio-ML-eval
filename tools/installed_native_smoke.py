#!/usr/bin/env python3
"""Smoke-test an installed wheel plus its native reasoner extra."""

from __future__ import annotations

import argparse
import gc
import json
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pyowl_core

import oaei_bioml_eval
from oaei_bioml_eval.coherence import score_reference_coherence
from oaei_bioml_eval.coherence.bridge import (
    Correspondence,
    compose_alignment_views,
)
from oaei_bioml_eval.coherence.provenance import sorted_line_sha256
from oaei_bioml_eval.coherence.report import ReasonerName

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
A = "http://ex.org/A"
B = "http://ex.org/B"

MATRIX_SCHEMA = "oaei-bioml-eval.installed-native-owner-matrix/1"
OWNER_MATRIX_SCHEMA = "oaei-bioml-eval.installed-native-format-owner-matrix/1"
REGRESSION_MATRIX_SCHEMA = "oaei-bioml-eval.installed-native-regression-matrix/1"
_ENCODED_SCHEMA = "pyowl-core/structural-columns"
_ENCODED_DESCRIPTOR_SHA256 = (
    "9ad29db6a7e616f65cea2957bc5ba8d1f9b99ef0eb1fe1432c09be25786267b5"
)
_ENCODED_BUFFER_WIDTHS = {
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
}
_EXPECTED_REASONER_SCHEMA = {
    "buffer_widths": _ENCODED_BUFFER_WIDTHS,
    "descriptor_sha256": _ENCODED_DESCRIPTOR_SHA256,
    "model_schema": 1,
    "schema_name": _ENCODED_SCHEMA,
    "schema_version": 1,
}
_REQUIRED_PUBLIC_COUNTERS = (
    "base_flattening_bytes",
    "parser_calls",
    "per_row_ffi_calls",
    "resolver_calls",
    "wire_decoder_calls",
    "wire_encoder_calls",
)
_MATERIALIZATION_COUNTERS = (
    "materialized_scalar_rows",
    "scalar_axiom_materializations",
)
_COPY_COUNTERS = ("encoded_staging_copy_bytes", "structural_copy_bytes")
_FORBIDDEN_ZERO_COUNTERS = frozenset(
    {
        *_REQUIRED_PUBLIC_COUNTERS,
        *_MATERIALIZATION_COUNTERS,
        "encoded_private_ir_bytes",
        "scalar_term_materializations",
        "structural_copy_bytes",
    }
)

FORMAT_FIXTURES: Mapping[str, tuple[bytes, bytes]] = {
    "functional": (
        b"""\
Prefix(:=<http://ex.org/>)
Ontology(<http://ex.org/clash-source>
  Declaration(Class(:A))
  Declaration(Class(:B))
  Declaration(Class(:C))
  DisjointClasses(:A :B)
)
""",
        b"""\
Prefix(:=<http://ex.org/>)
Ontology(<http://ex.org/clash-target>
  Declaration(Class(:B))
  Declaration(Class(:D))
)
""",
    ),
    "owlxml": (
        b"""\
<?xml version="1.0"?>
<Ontology xmlns="http://www.w3.org/2002/07/owl#"
    ontologyIRI="http://ex.org/clash-source">
  <Prefix name="ex:" IRI="http://ex.org/"/>
  <Declaration><Class abbreviatedIRI="ex:A"/></Declaration>
  <Declaration><Class abbreviatedIRI="ex:B"/></Declaration>
  <Declaration><Class abbreviatedIRI="ex:C"/></Declaration>
  <DisjointClasses>
    <Class abbreviatedIRI="ex:A"/>
    <Class abbreviatedIRI="ex:B"/>
  </DisjointClasses>
</Ontology>
""",
        b"""\
<?xml version="1.0"?>
<Ontology xmlns="http://www.w3.org/2002/07/owl#"
    ontologyIRI="http://ex.org/clash-target">
  <Prefix name="ex:" IRI="http://ex.org/"/>
  <Declaration><Class abbreviatedIRI="ex:B"/></Declaration>
  <Declaration><Class abbreviatedIRI="ex:D"/></Declaration>
</Ontology>
""",
    ),
    "rdfxml": (
        b"""\
<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="http://ex.org/clash-source"/>
  <owl:Class rdf:about="http://ex.org/A">
    <owl:disjointWith rdf:resource="http://ex.org/B"/>
  </owl:Class>
  <owl:Class rdf:about="http://ex.org/B"/>
  <owl:Class rdf:about="http://ex.org/C"/>
</rdf:RDF>
""",
        b"""\
<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="http://ex.org/clash-target"/>
  <owl:Class rdf:about="http://ex.org/B"/>
  <owl:Class rdf:about="http://ex.org/D"/>
</rdf:RDF>
""",
    ),
    "turtle": (
        b"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex: <http://ex.org/> .
<http://ex.org/clash-source> a owl:Ontology .
ex:A a owl:Class ; owl:disjointWith ex:B .
ex:B a owl:Class .
ex:C a owl:Class .
""",
        b"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex: <http://ex.org/> .
<http://ex.org/clash-target> a owl:Ontology .
ex:B a owl:Class .
ex:D a owl:Class .
""",
    ),
}


def _fingerprints(view: object) -> dict[str, str]:
    selected = cast(Any, view)
    return {
        "logical": selected.logical_fingerprint.hex,
        "signature": selected.signature_fingerprint.hex,
        "structural": selected.structural_fingerprint.hex,
    }


def _semantic_result(report: Mapping[str, object]) -> dict[str, object]:
    provenance = cast(Mapping[str, Any], report["provenance"])
    result = cast(Mapping[str, object], provenance["result"])
    return {
        "global_coherence": report["global_coherence"],
        "inconsistent": report["inconsistent"],
        "reasoner_used": report["reasoner_used"],
        "union_class_count": report["union_class_count"],
        "unsatisfiable_count": report["unsatisfiable_count"],
        "denominator_sha256": result["denominator_sha256"],
        "numerator_sha256": result["numerator_sha256"],
    }


def _require_encoded_handoff(
    handoff: Mapping[str, object],
    *,
    format_name: str,
    reasoner: str,
) -> None:
    label = f"{format_name}/{reasoner}"
    if handoff.get("owner_kind") != "composite":
        raise RuntimeError(f"{label} did not retain the public composite owner")
    schemas = handoff.get("core_encoded_view_schemas")
    if not isinstance(schemas, Mapping) or schemas.get(_ENCODED_SCHEMA) != 1:
        raise RuntimeError(f"{label} lacks the frozen encoded structural schema")
    reasoner_schema = handoff.get("reasoner_encoded_schema")
    if reasoner_schema != _EXPECTED_REASONER_SCHEMA:
        raise RuntimeError(
            f"{label} did not negotiate the exact public reasoner encoded schema"
        )
    if handoff.get("ingestion_path") != "encoded-native":
        raise RuntimeError(f"{label} did not select encoded-native ingestion")
    counters = handoff.get("counters")
    if not isinstance(counters, Mapping):
        raise RuntimeError(f"{label} did not publish compiler counters")
    missing = tuple(name for name in _REQUIRED_PUBLIC_COUNTERS if name not in counters)
    missing_groups = tuple(
        group
        for group, alternatives in (
            ("scalar_materialization", _MATERIALIZATION_COUNTERS),
            ("structural_copy", _COPY_COUNTERS),
        )
        if not any(name in counters for name in alternatives)
    )
    if missing or missing_groups:
        raise RuntimeError(
            f"{label} compiler counters are incomplete: "
            f"fields={missing!r}, groups={missing_groups!r}"
        )
    nonzero = {
        name: value
        for name, value in counters.items()
        if name in _FORBIDDEN_ZERO_COUNTERS
        and value is not False
        and not (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value == 0
        )
    }
    if nonzero:
        raise RuntimeError(f"{label} crossed forbidden work counters: {nonzero!r}")
    buffer_count = counters.get("encoded_buffer_count")
    zero_copy_count = counters.get("encoded_zero_copy_buffers")
    if (
        isinstance(buffer_count, bool)
        or not isinstance(buffer_count, int)
        or buffer_count < 1
        or zero_copy_count != buffer_count
    ):
        raise RuntimeError(f"{label} did not retain every encoded buffer zero-copy")


@contextmanager
def _owner_pairs(source: object, target: object) -> Iterator[Mapping[str, tuple[Any, Any]]]:
    source_wire = pyowl_core.encode_snapshot(cast(Any, source))
    target_wire = pyowl_core.encode_snapshot(cast(Any, target))
    decoded_source = pyowl_core.decode_snapshot(source_wire)
    decoded_target = pyowl_core.decode_snapshot(target_wire)
    overlay_source = pyowl_core.apply_delta(
        cast(Any, source),
        pyowl_core.OntologyDelta(),
    )
    overlay_target = pyowl_core.apply_delta(
        cast(Any, target),
        pyowl_core.OntologyDelta(),
    )
    with tempfile.TemporaryDirectory(prefix="oaei-installed-owner-matrix-") as temporary:
        directory = Path(temporary)
        source_path = directory / "source.pyocore"
        target_path = directory / "target.pyocore"
        source_path.write_bytes(source_wire)
        target_path.write_bytes(target_wire)
        mapped_source = pyowl_core.open_snapshot(source_path, mmap=True, verify=True)
        mapped_target = pyowl_core.open_snapshot(target_path, mmap=True, verify=True)
        try:
            yield {
                "decoded": (decoded_source, decoded_target),
                "direct": (source, target),
                "mmap": (mapped_source, mapped_target),
                "overlay": (overlay_source, overlay_target),
            }
        finally:
            gc.collect()
            try:
                mapped_target.close()
            finally:
                mapped_source.close()


def _score_owner_pair(
    source: Any,
    target: Any,
    *,
    format_name: str,
    owner_name: str,
    require_encoded: bool,
    correspondences: Iterable[Correspondence] = ((A, B, "="),),
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    retained_correspondences = tuple(correspondences)
    composite = compose_alignment_views(source, target, retained_correspondences)
    members = tuple(member.view for member in composite.members)
    if members != (source, target) or members[0] is not source or members[1] is not target:
        raise RuntimeError(
            f"{format_name}/{owner_name} composition lost source/target owner identity"
        )

    reasoner_results: dict[str, dict[str, object]] = {}
    for reasoner in ("hermit", "elk"):
        report = score_reference_coherence(
            retained_correspondences,
            source,
            target,
            reasoner=cast(ReasonerName, reasoner),
            timeout_s=None,
        )
        semantic = _semantic_result(report)
        provenance = cast(Mapping[str, Any], report["provenance"])
        handoff = cast(Mapping[str, object], provenance["compiler_handoff"])
        if require_encoded:
            _require_encoded_handoff(
                handoff,
                format_name=f"{format_name}/{owner_name}",
                reasoner=reasoner,
            )
        reasoner_results[reasoner] = {
            **semantic,
            "compiler_handoff": dict(handoff),
        }
    composite_fingerprints = _fingerprints(composite)
    del composite
    return (
        {
            "composite_owner_identity": True,
            "composite_fingerprints": composite_fingerprints,
            "source_fingerprints": _fingerprints(source),
            "target_fingerprints": _fingerprints(target),
            "reasoners": reasoner_results,
        },
        {
            reasoner: {
                name: value
                for name, value in result.items()
                if name != "compiler_handoff"
            }
            for reasoner, result in reasoner_results.items()
        },
    )


def run() -> dict[str, dict[str, object]]:
    """Run the frozen equivalence clash through both installed adapters."""

    source = pyowl_core.coerce_snapshot(
        (FIXTURES / "clash-source.ofn").read_bytes(),
        document_iri="urn:oaei:installed-smoke:source",
    )
    target = pyowl_core.coerce_snapshot(
        (FIXTURES / "clash-target.ofn").read_bytes(),
        document_iri="urn:oaei:installed-smoke:target",
    )
    output: dict[str, dict[str, object]] = {}
    for reasoner, timeout in (("hermit", None), ("elk", 60.0)):
        report = score_reference_coherence(
            [(A, B, "=")],
            source,
            target,
            reasoner=cast(ReasonerName, reasoner),
            timeout_s=timeout,
        )
        if report["reasoner_used"] != reasoner:
            raise RuntimeError(f"{reasoner} smoke used {report['reasoner_used']!r}")
        if (report["unsatisfiable_count"], report["union_class_count"]) != (2, 4):
            raise RuntimeError(f"{reasoner} smoke changed the frozen clash: {report}")
        provenance = cast(dict[str, Any], report["provenance"])
        metric = cast(dict[str, Any], provenance["metric"])
        if metric["package_version"] != oaei_bioml_eval.__version__:
            raise RuntimeError("installed provenance version does not match the package")
        reasoner_record = cast(dict[str, Any], provenance["reasoner"])
        transport = cast(dict[str, Any], reasoner_record["transport"])
        if reasoner == "hermit" and transport != {"mode": "in-process-identity"}:
            raise RuntimeError(f"unexpected HermiT transport: {transport!r}")
        if reasoner == "elk" and not (
            transport.get("mode") == "core-wire-worker"
            and transport.get("wire_verified") is True
            and transport.get("owl_parse_count") == 0
            and cast(int, transport.get("wire_bytes", 0)) > 0
        ):
            raise RuntimeError(f"unverified ELK transport: {transport!r}")
        output[reasoner] = {
            "global_coherence": report["global_coherence"],
            "reasoner_used": report["reasoner_used"],
            "union_class_count": report["union_class_count"],
            "unsatisfiable_count": report["unsatisfiable_count"],
        }
    return output


def run_format_matrix(*, require_encoded: bool = False) -> dict[str, object]:
    """Cross four equivalent syntaxes through public composite/reasoner facades."""

    formats: dict[str, dict[str, object]] = {}
    expected_source: dict[str, str] | None = None
    expected_target: dict[str, str] | None = None
    expected_results: dict[str, dict[str, object]] | None = None
    for format_name, (source_bytes, target_bytes) in FORMAT_FIXTURES.items():
        source = pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:installed-matrix:source",
        )
        target = pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:installed-matrix:target",
        )
        source_fingerprints = _fingerprints(source)
        target_fingerprints = _fingerprints(target)
        evidence, semantic_results = _score_owner_pair(
            source,
            target,
            format_name=format_name,
            owner_name="direct",
            require_encoded=require_encoded,
        )
        if expected_source is None:
            expected_source = source_fingerprints
            expected_target = target_fingerprints
            expected_results = semantic_results
        elif (
            source_fingerprints != expected_source
            or target_fingerprints != expected_target
            or semantic_results != expected_results
        ):
            raise RuntimeError(f"{format_name} diverged from the format-equivalent baseline")
        formats[format_name] = evidence

    return {
        "schema": MATRIX_SCHEMA,
        "encoded_required": require_encoded,
        "format_semantic_identity": True,
        "formats": formats,
    }


def run_format_owner_matrix(*, require_encoded: bool = False) -> dict[str, object]:
    """Cross every format through direct, decoded, mmap, and overlay owner pairs."""

    formats: dict[str, dict[str, object]] = {}
    expected_source: dict[str, str] | None = None
    expected_target: dict[str, str] | None = None
    expected_composite: dict[str, str] | None = None
    expected_results: dict[str, dict[str, object]] | None = None
    for format_name, (source_bytes, target_bytes) in FORMAT_FIXTURES.items():
        source = pyowl_core.coerce_snapshot(
            source_bytes,
            document_iri="urn:oaei:installed-owner-matrix:source",
        )
        target = pyowl_core.coerce_snapshot(
            target_bytes,
            document_iri="urn:oaei:installed-owner-matrix:target",
        )
        owners: dict[str, dict[str, object]] = {}
        with _owner_pairs(source, target) as pairs:
            for owner_name, (owned_source, owned_target) in pairs.items():
                evidence, semantic_results = _score_owner_pair(
                    owned_source,
                    owned_target,
                    format_name=format_name,
                    owner_name=owner_name,
                    require_encoded=require_encoded,
                )
                source_fingerprints = cast(dict[str, str], evidence["source_fingerprints"])
                target_fingerprints = cast(dict[str, str], evidence["target_fingerprints"])
                composite_fingerprints = cast(
                    dict[str, str],
                    evidence["composite_fingerprints"],
                )
                if expected_source is None:
                    expected_source = source_fingerprints
                    expected_target = target_fingerprints
                    expected_composite = composite_fingerprints
                    expected_results = semantic_results
                elif (
                    source_fingerprints != expected_source
                    or target_fingerprints != expected_target
                    or composite_fingerprints != expected_composite
                    or semantic_results != expected_results
                ):
                    raise RuntimeError(
                        f"{format_name}/{owner_name} diverged from the owner-equivalent baseline"
                    )
                owners[owner_name] = evidence
        formats[format_name] = {"owners": owners}

    return {
        "schema": OWNER_MATRIX_SCHEMA,
        "encoded_required": require_encoded,
        "format_owner_semantic_identity": True,
        "formats": formats,
    }


def run_regression_matrix(*, require_encoded: bool = False) -> dict[str, object]:
    """Run every pinned semantic case, including multi-level incoherence."""

    baseline_path = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
    baseline = cast(Mapping[str, Any], json.loads(baseline_path.read_text(encoding="utf-8")))
    cases = cast(Mapping[str, Mapping[str, Any]], baseline["cases"])
    observed: dict[str, dict[str, object]] = {}
    for case_name, expected in sorted(cases.items()):
        source = pyowl_core.coerce_snapshot(
            FIXTURES / str(expected["source"]),
            document_iri=f"urn:oaei:installed-regression:{case_name}:source",
        )
        target = pyowl_core.coerce_snapshot(
            FIXTURES / str(expected["target"]),
            document_iri=f"urn:oaei:installed-regression:{case_name}:target",
        )
        correspondences = tuple(
            cast(Correspondence, tuple(item))
            for item in cast(Iterable[Iterable[str]], expected["bridge"])
        )
        evidence, _semantic_results = _score_owner_pair(
            source,
            target,
            format_name=f"regression:{case_name}",
            owner_name="direct",
            require_encoded=require_encoded,
            correspondences=correspondences,
        )
        reasoners = cast(Mapping[str, Mapping[str, Any]], evidence["reasoners"])
        named_classes = tuple(cast(Iterable[str], expected["named_classes"]))
        for reasoner, result in reasoners.items():
            wanted = tuple(cast(Iterable[str], expected[f"{reasoner}_unsatisfiable"]))
            if (
                result["reasoner_used"] != reasoner
                or result["union_class_count"] != len(named_classes)
                or result["unsatisfiable_count"] != len(wanted)
                or result["denominator_sha256"] != sorted_line_sha256(named_classes)
                or result["numerator_sha256"] != sorted_line_sha256(wanted)
            ):
                raise RuntimeError(
                    f"{case_name}/{reasoner} diverged from the pinned semantic oracle"
                )
        observed[case_name] = evidence

    required = {
        "already_incoherent",
        "equivalence_clash",
        "equivalence_clean",
        "subsumption_forward_clash",
        "subsumption_reverse_clash",
    }
    if set(observed) != required:
        raise RuntimeError(
            f"installed semantic baseline has unexpected cases: {sorted(observed)!r}"
        )
    return {
        "schema": REGRESSION_MATRIX_SCHEMA,
        "encoded_required": require_encoded,
        "pinned_semantic_parity": True,
        "cases": observed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--matrix",
        action="store_true",
        help="cross format-equivalent Functional, RDF/XML, Turtle, and OWL/XML owners",
    )
    mode.add_argument(
        "--owner-matrix",
        action="store_true",
        help="also cross direct, decoded, mmap, and overlay source/target owners",
    )
    mode.add_argument(
        "--regression-matrix",
        action="store_true",
        help="run every pinned semantic case, including multi-level incoherence",
    )
    parser.add_argument(
        "--require-encoded",
        action="store_true",
        help="fail unless every matrix lane publishes complete encoded-native zero-work evidence",
    )
    args = parser.parse_args(argv)
    if args.require_encoded and not (
        args.matrix or args.owner_matrix or args.regression_matrix
    ):
        parser.error(
            "--require-encoded requires --matrix, --owner-matrix, or --regression-matrix"
        )
    if args.owner_matrix:
        result = run_format_owner_matrix(require_encoded=args.require_encoded)
    elif args.regression_matrix:
        result = run_regression_matrix(require_encoded=args.require_encoded)
    elif args.matrix:
        result = run_format_matrix(require_encoded=args.require_encoded)
    else:
        result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
