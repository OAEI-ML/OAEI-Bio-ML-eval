#!/usr/bin/env python3
"""Compare native coherence with the pinned public NCIT-DOID migration evidence.

This is a Java-free manual release gate. The public ontology/alignment files stay
outside Git; their pinned hashes prevent an accidental comparison with different
data. It consumes only the released pyowl-core wire and pyHermiT/pyELK public
facades, and fails at the explicit capability boundary if any is incompatible.
``--timeout`` retains the reasoner-specific public contract. Alternatively,
``--overall-timeout`` bounds this tool's complete capture, parse, compose,
reason, comparison, and reporting workflow in one terminable process; that
mode requires ``--timeout none`` so termination cannot orphan a nested worker.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import multiprocessing
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.coherence.bridge import coerce_ontology_pair_once  # noqa: E402
from oaei_bioml_eval.coherence.loaders import parse_relation_typed_tsv  # noqa: E402
from oaei_bioml_eval.coherence.report import score_reference_coherence  # noqa: E402

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10-ncit-doid-train.json"
_MAX_WORKER_RESPONSE_BYTES = 16 * 1024 * 1024
_WORKER_SHUTDOWN_SECONDS = 1.0


class ComparatorTimeoutError(TimeoutError):
    """The complete standalone file-to-report comparison exceeded its bound."""


class ComparatorWorkerError(RuntimeError):
    """The complete-comparison worker failed without a valid result."""


@dataclass(frozen=True, slots=True)
class ComparatorRequest:
    """Small path/configuration request for one independently terminable run."""

    source: Path
    target: Path
    alignment: Path
    baseline: Path
    reasoner: Literal["hermit", "elk"]
    reasoner_timeout_s: float | None
    require_accelerated: bool = True


ComparatorEntrypoint = Callable[[Connection, ComparatorRequest], None]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capture_inputs(
    paths: Mapping[str, Path],
) -> tuple[dict[str, bytes], dict[str, dict[str, object]]]:
    """Read each input once and bind the exact retained bytes to release evidence."""

    payloads: dict[str, bytes] = {}
    evidence: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        resolved = path.expanduser().resolve()
        payload = resolved.read_bytes()
        payloads[name] = payload
        evidence[name] = {
            "filename": resolved.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    return payloads, evidence


def _runtime_evidence(
    report: Mapping[str, Any],
    *,
    reasoner: Literal["hermit", "elk"],
    require_accelerated: bool,
) -> dict[str, Any]:
    """Fail closed for a native release run and hash the loaded extension."""

    provenance = report.get("provenance")
    reasoner_record = provenance.get("reasoner") if isinstance(provenance, Mapping) else None
    backend = reasoner_record.get("backend") if isinstance(reasoner_record, Mapping) else None
    backend_evidence = dict(backend) if isinstance(backend, Mapping) else {}
    expected_names = {"elk": {"rust"}, "hermit": {"native", "verify"}}[reasoner]
    accelerated = backend_evidence.get("accelerated") is True
    if require_accelerated and (
        not accelerated or backend_evidence.get("name") not in expected_names
    ):
        raise RuntimeError(
            f"{reasoner} release comparison requires an accelerated native backend; "
            "use --allow-python only for a diagnostic run"
        )

    artifact: dict[str, object] | None = None
    if accelerated:
        module_name = {"elk": "pyelk._native", "hermit": "pyhermit._native"}[reasoner]
        spec = importlib.util.find_spec(module_name)
        origin = None if spec is None else spec.origin
        if not isinstance(origin, str):
            raise RuntimeError(f"accelerated backend did not expose {module_name}.__spec__.origin")
        path = Path(origin).resolve(strict=True)
        payload = path.read_bytes()
        artifact = {
            "module": module_name,
            "filename": path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    return {
        "accelerated_required": require_accelerated,
        "backend": backend_evidence,
        "native_artifact": artifact,
    }


def compare_report(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    reasoner: str,
    input_evidence: Mapping[str, Mapping[str, object]],
) -> dict[str, Any]:
    """Fail on any frozen semantic count/digest mismatch."""

    run = baseline["runs"][reasoner]
    expected = {
        "union_class_count": baseline["inputs"]["named_class_count"],
        "asserted_correspondences": baseline["bridge"]["mapping_count"],
        "unsatisfiable_count": run["unsatisfiable_count"],
        "numerator_sha256": run["unsatisfiable_sha256"],
    }
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("schema") != "coherence-provenance/1":
        raise RuntimeError("native report lacks coherence-provenance/1")
    result = provenance.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("native report lacks provenance result digests")
    actual = {
        "reasoner_used": report.get("reasoner_used"),
        "union_class_count": report.get("union_class_count"),
        "asserted_correspondences": report.get("asserted_correspondences"),
        "unsatisfiable_count": report.get("unsatisfiable_count"),
        "numerator_sha256": result.get("numerator_sha256"),
    }
    mismatches = {
        key: {"actual": actual[key], "expected": expected[key]}
        for key in expected
        if actual[key] != expected[key]
    }
    if actual["reasoner_used"] != reasoner:
        mismatches["reasoner_used"] = {
            "actual": actual["reasoner_used"],
            "expected": reasoner,
        }
    if mismatches:
        raise RuntimeError(
            "native result differs from the frozen NCIT-DOID evidence: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return {
        "schema": "oaei-bioml-eval.native-comparison/2",
        "baseline_schema": baseline["schema"],
        "reasoner": reasoner,
        "agreement": True,
        "inputs": dict(input_evidence),
        "counts": {
            "named_classes": actual["union_class_count"],
            "correspondences": actual["asserted_correspondences"],
            "unsatisfiable": actual["unsatisfiable_count"],
        },
        "unsatisfiable_sha256": actual["numerator_sha256"],
        "provenance": provenance,
    }


def _compare(request: ComparatorRequest) -> dict[str, Any]:
    started = time.perf_counter()
    baseline_path = request.baseline.expanduser().resolve()
    baseline_payload = baseline_path.read_bytes()
    baseline = json.loads(baseline_payload)
    baseline_evidence = {
        "filename": baseline_path.name,
        "sha256": hashlib.sha256(baseline_payload).hexdigest(),
        "bytes": len(baseline_payload),
    }
    payloads, input_evidence = _capture_inputs(
        {
            "source": request.source,
            "target": request.target,
            "alignment": request.alignment,
        }
    )
    pinned_hashes = {
        "source": baseline["inputs"]["source"]["sha256"],
        "target": baseline["inputs"]["target"]["sha256"],
        "alignment": baseline["bridge"]["sha256"],
    }
    actual_hashes = {name: evidence["sha256"] for name, evidence in input_evidence.items()}
    if actual_hashes != pinned_hashes:
        raise ValueError(
            "input hashes do not match the pinned public NCIT-DOID evidence: "
            + json.dumps({"actual": actual_hashes, "expected": pinned_hashes}, sort_keys=True)
        )
    if request.alignment.suffix.lower() != ".tsv":
        raise ValueError("the frozen NCIT-DOID comparator requires its pinned TSV alignment")

    correspondences = parse_relation_typed_tsv(payloads["alignment"])
    source, target = coerce_ontology_pair_once(
        payloads["source"],
        payloads["target"],
        source_document_iri="urn:oaei:ncit",
        target_document_iri="urn:oaei:doid",
        load_options=None,
        resolver=None,
        cancellation_token=None,
    )
    del payloads
    report = score_reference_coherence(
        correspondences,
        source,
        target,
        reasoner=request.reasoner,
        timeout_s=request.reasoner_timeout_s,
    )
    comparison = compare_report(
        report,
        baseline,
        reasoner=request.reasoner,
        input_evidence=input_evidence,
    )
    comparison["baseline"] = baseline_evidence
    comparison["runtime"] = _runtime_evidence(
        report,
        reasoner=request.reasoner,
        require_accelerated=request.require_accelerated,
    )
    comparison["execution"] = {"worker_elapsed_seconds": time.perf_counter() - started}
    return comparison


def _compare_worker_entry(connection: Connection, request: ComparatorRequest) -> None:
    """Own parsing through reporting in one process that the parent can kill."""

    try:
        response: dict[str, Any] = {"ok": True, "result": _compare(request)}
    except BaseException as error:
        response = {
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
    try:
        connection.send_bytes(
            json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    finally:
        connection.close()


def _terminate_process(process: Any) -> None:
    if not process.is_alive():
        process.join()
        return
    process.terminate()
    process.join(_WORKER_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_WORKER_SHUTDOWN_SECONDS)


def run_bounded_comparison(
    request: ComparatorRequest,
    *,
    overall_timeout_s: float,
    entrypoint: ComparatorEntrypoint | None = None,
) -> dict[str, Any]:
    """Bound the standalone parse/compose/reason/report workflow wall clock."""

    if request.reasoner_timeout_s is not None:
        raise ValueError(
            "--overall-timeout requires --timeout none so the bounded comparator "
            "owns the only worker process and cannot orphan a nested reasoner"
        )
    if not isinstance(overall_timeout_s, (int, float)) or isinstance(overall_timeout_s, bool):
        raise TypeError("overall_timeout_s must be a positive number")
    if not math.isfinite(overall_timeout_s) or overall_timeout_s <= 0:
        raise ValueError("overall_timeout_s must be positive")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=entrypoint or _compare_worker_entry,
        args=(child, request),
        name="oaei-native-comparator",
    )
    try:
        process.start()
        child.close()
        if not parent.poll(overall_timeout_s):
            _terminate_process(process)
            raise ComparatorTimeoutError(
                "complete NCIT-DOID comparison exceeded "
                f"{overall_timeout_s}s (the inner reasoner is unbounded by construction)"
            )
        try:
            payload = parent.recv_bytes(_MAX_WORKER_RESPONSE_BYTES)
        except (EOFError, OSError) as error:
            _terminate_process(process)
            raise ComparatorWorkerError(
                "complete-comparison worker exited without a valid response"
            ) from error
        process.join(_WORKER_SHUTDOWN_SECONDS)
        if process.is_alive():
            _terminate_process(process)
            raise ComparatorWorkerError(
                "complete-comparison worker did not exit after returning a result"
            )
        if process.exitcode != 0:
            raise ComparatorWorkerError(
                f"complete-comparison worker exited with status {process.exitcode}"
            )
        try:
            response = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ComparatorWorkerError(
                "complete-comparison worker returned malformed JSON"
            ) from error
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise ComparatorWorkerError(
                "complete-comparison worker returned an invalid response envelope"
            )
        if response["ok"] is not True:
            detail = response.get("error")
            raise ComparatorWorkerError(f"complete-comparison worker failed: {detail}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ComparatorWorkerError(
                "complete-comparison worker returned an invalid comparison result"
            )
        return result
    finally:
        parent.close()
        child.close()
        if process.is_alive():
            _terminate_process(process)


def _timeout(value: str) -> float | None:
    if value.lower() in {"none", "unbounded"}:
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive or 'none'")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--alignment", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional new path for the exact schema-2 JSON evidence; existing files are refused",
    )
    parser.add_argument("--reasoner", choices=("hermit", "elk"), required=True)
    parser.add_argument("--timeout", type=_timeout, default=7200.0)
    parser.add_argument(
        "--allow-python",
        action="store_true",
        help="allow a non-accelerated diagnostic result; native release runs fail closed by default",
    )
    parser.add_argument(
        "--overall-timeout",
        type=_timeout,
        default=None,
        help=(
            "optional wall-clock bound for the complete standalone parse/compose/reason/report "
            "workflow; requires --timeout none to avoid a nested worker"
        ),
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    request = ComparatorRequest(
        source=args.source,
        target=args.target,
        alignment=args.alignment,
        baseline=args.baseline,
        reasoner=args.reasoner,
        reasoner_timeout_s=args.timeout,
        require_accelerated=not args.allow_python,
    )
    comparison = (
        _compare(request)
        if args.overall_timeout is None
        else run_bounded_comparison(request, overall_timeout_s=args.overall_timeout)
    )
    execution = comparison.setdefault("execution", {})
    if not isinstance(execution, dict):
        raise ComparatorWorkerError("comparison result has invalid execution evidence")
    execution["driver_pre_serialization_elapsed_seconds"] = time.perf_counter() - started
    execution["reasoner_timeout_seconds"] = args.timeout
    execution["overall_timeout_seconds"] = args.overall_timeout
    rendered = json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
