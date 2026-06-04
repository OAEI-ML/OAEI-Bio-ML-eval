"""
shared bits for the typed-scorer tests: where the on-disk Track 2 fixtures live,
and a couple of tiny in-memory builders. the fixtures encode a fully hand-traced
2-query scenario (Q0 eq + Q1 sub) — the expected metric values are derived from
that trace in the test modules.
"""
from __future__ import annotations

import csv
from pathlib import Path

from oaei_bioml_eval.hierarchy import HierarchyIndex

FIXTURES = Path(__file__).parent / "fixtures" / "track2"


def chain_hierarchy() -> HierarchyIndex:
    # P2 -> P1 -> T -> C1 -> C2  (parent_id is the broader class)
    return HierarchyIndex([
        {"child_id": "T", "parent_id": "P1"},
        {"child_id": "P1", "parent_id": "P2"},
        {"child_id": "C1", "parent_id": "T"},
        {"child_id": "C2", "parent_id": "C1"},
    ])


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
