#!/usr/bin/env python3
"""Smoke-test an installed wheel plus its native reasoner extra."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pyowl_core

import oaei_bioml_eval
from oaei_bioml_eval.coherence import score_reference_coherence
from oaei_bioml_eval.coherence.report import ReasonerName

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
A = "http://ex.org/A"
B = "http://ex.org/B"


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


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
