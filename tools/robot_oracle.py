#!/usr/bin/env python3
"""Regenerate the quarantined ROBOT 1.9.10 coherence oracle baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.coherence.bridge import (  # noqa: E402
    Correspondence,
    normalize_correspondences,
)
BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
IRI = "http://ex.org/"
OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"
_TRIVIAL = frozenset({OWL_THING, OWL_NOTHING})
_TERMINATE_GRACE_SECONDS = 10.0
_CLASS_SIGNATURE_SPARQL = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "SELECT ?c WHERE { ?c a owl:Class . FILTER(isIRI(?c)) }\n"
)
_UNSAT_IRI = re.compile(r"unsatisfiable:\s*(\S+)")
_UNSAT_TOTAL = re.compile(r"There are (\d+) unsatisfiable")


@dataclass
class _MergedOntology:
    handle: Path
    workdir: Path


@dataclass(frozen=True)
class _OracleResult:
    unsatisfiable: tuple[str, ...]


def _read_iri_column(path: Path) -> set[str]:
    iris: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            iri = line.strip()
            if iri.startswith("<") and iri.endswith(">"):
                iri = iri[1:-1]
            if iri:
                iris.add(iri)
    return iris


def _parse_unsatisfiable(log: str) -> set[str]:
    listed = {match.group(1) for match in _UNSAT_IRI.finditer(log)}
    total = _UNSAT_TOTAL.search(log)
    if total is None and not listed:
        raise RuntimeError(
            "ROBOT reason exited non-zero without an unsatisfiable-class report:\n"
            + log[-2000:]
        )
    if total is not None and int(total.group(1)) != len(listed):
        raise RuntimeError(
            f"ROBOT reason reported {total.group(1)} unsatisfiable classes but "
            f"logged {len(listed)}"
        )
    return listed - _TRIVIAL


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class _RobotOracleRuntime:
    """Repository-only ROBOT runtime; never imported by the package or CI."""

    def __init__(
        self,
        *,
        robot_jar: str | Path | None = None,
        java: str | None = None,
        heap: str | None = None,
        robot_cmd: list[str] | None = None,
    ) -> None:
        jar = robot_jar or os.environ.get("ROBOT_JAR")
        self._robot_jar = Path(jar) if jar else None
        self._java = java or os.environ.get("JAVA") or "java"
        self._heap = heap
        self._explicit = list(robot_cmd) if robot_cmd else None

    def _base_cmd(self) -> list[str]:
        if self._explicit:
            return list(self._explicit)
        on_path = shutil.which("robot")
        if on_path:
            return [on_path]
        if self._robot_jar and self._robot_jar.exists():
            heap = [f"-Xmx{self._heap}"] if self._heap else []
            return [self._java, *heap, "-jar", str(self._robot_jar)]
        raise FileNotFoundError("ROBOT not found for the manual oracle")

    def _env(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self._heap:
            environment["ROBOT_JAVA_ARGS"] = f"-Xmx{self._heap}"
        return environment

    def named_classes(self, merged: _MergedOntology) -> tuple[str, ...]:
        query = merged.workdir / "classes.rq"
        query.write_text(_CLASS_SIGNATURE_SPARQL, encoding="utf-8")
        output = merged.workdir / "classes.tsv"
        code, log = self._run(
            [
                *self._base_cmd(),
                "query",
                "--input",
                str(merged.handle),
                "--query",
                str(query),
                str(output),
            ],
            timeout_s=None,
            label="query:classes",
        )
        if code != 0:
            raise RuntimeError(
                f"ROBOT class-signature query failed (exit {code}):\n{log[-2000:]}"
            )
        return tuple(sorted(_read_iri_column(output) - _TRIVIAL))

    def unsatisfiable_classes(
        self,
        merged: _MergedOntology,
        *,
        which: str,
        timeout_s: float | None,
    ) -> _OracleResult:
        if which not in {"hermit", "elk"}:
            raise ValueError(f"unknown oracle reasoner: {which!r}")
        code, log = self._run(
            [
                *self._base_cmd(),
                "reason",
                "--reasoner",
                which,
                "--input",
                str(merged.handle),
            ],
            timeout_s=timeout_s,
            label=f"reason:{which}",
        )
        unsatisfiable = () if code == 0 else tuple(sorted(_parse_unsatisfiable(log)))
        return _OracleResult(unsatisfiable)

    def dispose(self, merged: _MergedOntology) -> None:
        shutil.rmtree(merged.workdir, ignore_errors=True)

    def _run(
        self, command: list[str], *, timeout_s: float | None, label: str
    ) -> tuple[int, str]:
        started = time.perf_counter()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=self._env(),
        )
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            elapsed = time.perf_counter() - started
            raise TimeoutError(
                f"ROBOT oracle {label} exceeded {timeout_s}s after {elapsed:.3f}s"
            ) from None
        return process.returncode, (output or b"").decode("utf-8", "replace")


def _write_bridge(path: Path, pairs: Iterable[object]) -> None:
    """Quarantined serializer used only to regenerate the pinned Java oracle."""

    ordered = normalize_correspondences(cast(Iterable[Correspondence], pairs))
    iris = sorted({iri for source, target, _ in ordered for iri in (source, target)})
    lines = ["Ontology(<https://w3id.org/oaei-bioml/coherence/oracle-bridge>"]
    lines.extend(f"  Declaration(Class(<{iri}>))" for iri in iris)
    for source, target, relation in ordered:
        if relation == "=":
            lines.append(f"  EquivalentClasses(<{source}> <{target}>)")
        elif relation == "<=":
            lines.append(f"  SubClassOf(<{source}> <{target}>)")
        else:
            lines.append(f"  SubClassOf(<{target}> <{source}>)")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class OracleRobotReasoner(_RobotOracleRuntime):
    """Repository-only legacy merge kept outside the installable package."""

    def merge(
        self,
        src_owl: Path,
        tgt_owl: Path,
        pairs: Iterable[object],
    ) -> _MergedOntology:
        workdir = Path(tempfile.mkdtemp(prefix="coh-robot-oracle-"))
        bridge = workdir / "bridge.ofn"
        merged = workdir / "merged.ttl"
        _write_bridge(bridge, pairs)
        code, log = self._run(
            [
                *self._base_cmd(),
                "merge",
                "--input",
                str(src_owl),
                "--input",
                str(tgt_owl),
                "--input",
                str(bridge),
                "--output",
                str(merged),
            ],
            timeout_s=None,
            label="oracle:merge",
        )
        if code != 0:
            self.dispose(_MergedOntology(merged, workdir))
            raise RuntimeError(f"ROBOT oracle merge failed (exit {code}):\n{log[-2000:]}")
        return _MergedOntology(merged, workdir)

CASES: dict[str, tuple[str, str, tuple[tuple[str, ...], ...]]] = {
    "equivalence_clash": (
        "clash-source.ofn",
        "clash-target.ofn",
        ((IRI + "A", IRI + "B", "="),),
    ),
    "equivalence_clean": (
        "clash-source.ofn",
        "clash-target.ofn",
        ((IRI + "A", IRI + "D", "="),),
    ),
    "subsumption_forward_clash": (
        "clash-source.ofn",
        "clash-target.ofn",
        ((IRI + "A", IRI + "B", "<="),),
    ),
    "subsumption_reverse_clash": (
        "clash-source.ofn",
        "clash-target.ofn",
        ((IRI + "A", IRI + "B", ">="),),
    ),
    "already_incoherent": (
        "incoherent-source.ofn",
        "empty-target.ofn",
        (),
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_output(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return completed.stdout.strip()


def capture(robot_jar: Path, java: Path, *, timeout_s: float) -> dict[str, Any]:
    """Run every small oracle case and return a time-free deterministic payload."""

    reasoner = OracleRobotReasoner(
        robot_jar=robot_jar,
        java=str(java),
        heap="4g",
    )
    cases: dict[str, Any] = {}
    for case_id, (source_name, target_name, pairs) in sorted(CASES.items()):
        source = FIXTURES / source_name
        target = FIXTURES / target_name
        merged = reasoner.merge(source, target, pairs)
        try:
            named = reasoner.named_classes(merged)
            hermit = reasoner.unsatisfiable_classes(
                merged, which="hermit", timeout_s=timeout_s
            )
            elk = reasoner.unsatisfiable_classes(
                merged, which="elk", timeout_s=timeout_s
            )
        finally:
            reasoner.dispose(merged)
        cases[case_id] = {
            "source": source_name,
            "source_sha256": _sha256(source),
            "target": target_name,
            "target_sha256": _sha256(target),
            "bridge": [list(item) for item in normalize_correspondences(pairs)],
            "named_classes": list(named),
            "hermit_unsatisfiable": list(hermit.unsatisfiable),
            "elk_unsatisfiable": list(elk.unsatisfiable),
        }
    return {
        "schema": "oaei-bioml-eval.robot-oracle/1",
        "robot": {
            "version": _command_output(
                [str(java), "-jar", str(robot_jar), "--version"]
            ),
            "sha256": _sha256(robot_jar),
            "release": "https://github.com/ontodev/robot/releases/tag/v1.9.10",
        },
        "java": _command_output([str(java), "-version"]),
        "comparison": {
            "class_order": "unicode-codepoint",
            "elapsed_time_is_contract": False,
            "owl_nothing_excluded": True,
        },
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-jar", required=True, type=Path)
    parser.add_argument("--java", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    first = render(capture(args.robot_jar, args.java, timeout_s=args.timeout))
    second = render(capture(args.robot_jar, args.java, timeout_s=args.timeout))
    if first != second:
        raise SystemExit("ROBOT oracle was not deterministic across consecutive runs")
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(first, encoding="utf-8")
        return 0
    if first != BASELINE.read_text(encoding="utf-8"):
        raise SystemExit("ROBOT oracle differs from the committed baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
