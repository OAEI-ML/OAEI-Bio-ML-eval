#!/usr/bin/env python3
"""Regenerate the quarantined ROBOT 1.9.10 coherence oracle baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.coherence.reasoner import (  # noqa: E402
    RobotReasoner,
    normalize_correspondences,
)

BASELINE = ROOT / "tests" / "baselines" / "robot-1.9.10.json"
FIXTURES = ROOT / "tests" / "fixtures" / "coherence-oracle"
IRI = "http://ex.org/"

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

    reasoner = RobotReasoner(
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
