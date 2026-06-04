"""
oaei_bioml_eval.io: the small tsv/json helpers the typed scorer needs.

Vendored so the participant-facing scorer stays dependency-free —
the heavy candi-pool side keeps its own copy. Lifted from BioKG-Align's `io.py`,
trimmed to what the typed half actually reads and writes: TSV in/out, JSON out,
and the `[a, b]` list-literal columns the answers/cands files use.
"""
from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
from typing import Any, Iterable


def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_tsv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> None:
    rows = list(rows)
    if fieldnames is None:                       # infer from the first row
        fieldnames = list(rows[0].keys()) if rows else []
    ensure_dir(Path(path).parent)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    ensure_dir(Path(path).parent)
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_list(value: str) -> list[str]:
    """Parse a `['a', 'b']` cell back into a list of strings; '' -> []."""
    if value is None or value == "":
        return []
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, list):
        raise ValueError(f"expected a list literal, got: {value!r}")
    return [str(item) for item in parsed]


def list_literal(values: Iterable[str]) -> str:
    """Inverse of parse_list — render a list as the `['a', 'b']` cell form."""
    return repr(list(values))
