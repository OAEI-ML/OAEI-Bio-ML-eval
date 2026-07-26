#!/usr/bin/env python3
"""Smoke-test an installed wheel plus its native reasoner extra."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pyowl_core

import oaei_bioml_eval
from oaei_bioml_eval.coherence import score_reference_coherence
from oaei_bioml_eval.coherence.bridge import compose_alignment_views
from oaei_bioml_eval.coherence.report import ReasonerName

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
A = "http://ex.org/A"
B = "http://ex.org/B"

MATRIX_SCHEMA = "oaei-bioml-eval.installed-native-owner-matrix/1"
_ENCODED_SCHEMA = "pyowl-core/structural-columns"
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
        composite = compose_alignment_views(source, target, ((A, B, "="),))
        members = tuple(member.view for member in composite.members)
        if members != (source, target) or members[0] is not source or members[1] is not target:
            raise RuntimeError(f"{format_name} composition lost source/target owner identity")

        reasoner_results: dict[str, dict[str, object]] = {}
        for reasoner in ("hermit", "elk"):
            report = score_reference_coherence(
                [(A, B, "=")],
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
                    format_name=format_name,
                    reasoner=reasoner,
                )
            reasoner_results[reasoner] = {
                **semantic,
                "compiler_handoff": dict(handoff),
            }

        semantic_results = {
            reasoner: {
                name: value
                for name, value in result.items()
                if name != "compiler_handoff"
            }
            for reasoner, result in reasoner_results.items()
        }
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
        formats[format_name] = {
            "composite_owner_identity": True,
            "composite_fingerprints": _fingerprints(composite),
            "source_fingerprints": source_fingerprints,
            "target_fingerprints": target_fingerprints,
            "reasoners": reasoner_results,
        }

    return {
        "schema": MATRIX_SCHEMA,
        "encoded_required": require_encoded,
        "format_semantic_identity": True,
        "formats": formats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="cross format-equivalent Functional, RDF/XML, Turtle, and OWL/XML owners",
    )
    parser.add_argument(
        "--require-encoded",
        action="store_true",
        help="fail unless every matrix lane publishes complete encoded-native zero-work evidence",
    )
    args = parser.parse_args(argv)
    if args.require_encoded and not args.matrix:
        parser.error("--require-encoded requires --matrix")
    result = run_format_matrix(require_encoded=args.require_encoded) if args.matrix else run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
