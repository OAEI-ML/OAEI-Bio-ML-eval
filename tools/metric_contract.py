#!/usr/bin/env python3
"""Capture or verify the frozen typed/equivalence metric contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oaei_bioml_eval.equivalence.report import (  # noqa: E402
    score_global_files,
    score_local_files,
)
from oaei_bioml_eval.typed.report import score_files  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "metric-contract-v1.json"
EQUIVALENCE = ROOT / "tests" / "fixtures" / "metric-contract"
TYPED = ROOT / "tests" / "fixtures" / "track2"

INPUTS = (
    EQUIVALENCE / "equivalence-global-reference.tsv",
    EQUIVALENCE / "equivalence-global-submission.tsv",
    EQUIVALENCE / "equivalence-local-gold.tsv",
    EQUIVALENCE / "equivalence-local-submission.tsv",
    TYPED / "answers.tsv",
    TYPED / "hierarchy.tsv",
    TYPED / "submission.block.tsv",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_value(value: Any) -> Any:
    """Normalize published metric precision across supported interpreters."""

    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {key: _contract_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_contract_value(item) for item in value]
    return value


def capture() -> dict[str, Any]:
    """Return a deterministic contract payload for all frozen metric surfaces."""

    payload = {
        "schema": "oaei-bioml-eval.metric-contract/1",
        "baseline_version": "0.1.0.dev0",
        "inputs": {
            str(path.relative_to(ROOT)): _sha256(path) for path in sorted(INPUTS)
        },
        "results": {
            "equivalence_global": score_global_files(
                EQUIVALENCE / "equivalence-global-submission.tsv",
                EQUIVALENCE / "equivalence-global-reference.tsv",
            ),
            "equivalence_local": score_local_files(
                EQUIVALENCE / "equivalence-local-submission.tsv",
                EQUIVALENCE / "equivalence-local-gold.tsv",
                candidate_count=3,
            ),
            "typed_hierarchy": score_files(
                TYPED / "submission.block.tsv",
                TYPED / "answers.tsv",
                hierarchy_path=TYPED / "hierarchy.tsv",
                candidate_count=4,
            ),
        },
    }
    return cast(dict[str, Any], _contract_value(payload))


def render(payload: dict[str, Any]) -> str:
    """Render contract bytes canonically."""

    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the committed baseline deliberately instead of checking it.",
    )
    args = parser.parse_args(argv)
    actual = render(capture())
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(actual, encoding="utf-8")
        return 0
    if not BASELINE.is_file():
        raise SystemExit(f"missing metric contract: {BASELINE}")
    expected = BASELINE.read_text(encoding="utf-8")
    if actual != expected:
        raise SystemExit(
            "metric-contract/1 changed; inspect the semantic difference before "
            "running tools/metric_contract.py --write"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
