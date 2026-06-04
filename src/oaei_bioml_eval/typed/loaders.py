"""
oaei_bioml_eval.typed.loaders: read the Track 2 gold + submission off disk.

Everything keys on `(SrcEntity, QueryID)` — a source entity poses two queries (Q0
equivalence, Q1 subsumption-only), so the source id alone isn't unique. The gold
lives in `answers.tsv` (one row per query, carrying the per-query candidate pool);
the submission is the NeurIPS **4-column block** TSV (BIOML-ADR-09), whose QueryID
is recovered **positionally** against the answers row order. The graded-relevance
and preferred-pair files are optional — the scorer can re-derive both from the
answers + hierarchy via the frozen `relevance.py`.
"""
from __future__ import annotations

import math
import warnings
from collections import defaultdict
from pathlib import Path

from ..io import parse_list, read_tsv


def load_answers(
    path: str | Path,
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """
    Per-query gold from `answers.tsv` -> `{(SrcEntity, QueryID): {(Tgt, Rel)}}`.

    Accepts either the scalar (`TgtEntity`/`Relation`) or list (`TgtEntities`/
    `Relations`) column form; the list form lets a 1:many equivalence query carry
    several gold pairs. QueryID falls back to `"Q0"` when the column is absent.
    """
    answers: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in read_tsv(path):
        src = row["SrcEntity"]
        query_id = row.get("QueryID", "Q0")
        targets = (
            parse_list(row["TgtEntities"])
            if row.get("TgtEntities", "").startswith("[")
            else [row["TgtEntity"]]
        )
        relations = (
            parse_list(row["Relations"])
            if row.get("Relations", "").startswith("[")
            else [row["Relation"]]
        )
        if len(targets) != len(relations):
            raise ValueError(
                f"answers.tsv target/relation length mismatch for {src!r} "
                f"({len(targets)} targets vs {len(relations)} relations)."
            )
        for target, relation in zip(targets, relations):
            answers[(src, query_id)].add((target, relation))
    return dict(answers)


def load_per_query_candidate_sets(
    path: str | Path,
) -> dict[tuple[str, str], set[str]]:
    """
    Per-query candidate pools from a `cands.tsv` -> `{(Src, QueryID): {tgt}}`.

    Prefer `load_per_query_candidate_sets_from_answers` when the public
    `test.cands.tsv` has dropped the QueryID column — the answers file keeps it.
    """
    sets: dict[tuple[str, str], set[str]] = {}
    for row in read_tsv(path):
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        sets[key] = set(parse_list(row.get("TgtCandidates", "[]")))
    return sets


def load_per_query_candidate_sets_from_answers(
    answers_path: str | Path,
) -> dict[tuple[str, str], set[str]]:
    """Same shape, read off the answers file (which retains QueryID)."""
    sets: dict[tuple[str, str], set[str]] = {}
    for row in read_tsv(answers_path):
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        sets[key] = set(parse_list(row.get("TgtCandidates", "[]")))
    return sets


def load_preferred_pairs(
    path: str | Path,
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """
    Preferred (Tgt, Rel) gold from `preferred.tsv` -> `{(Src, QueryID): [pair]}`,
    deduplicated and sorted per query. Returns `{}` if the file is absent — the
    scorer then derives the pairs from the answers via `select_preferred_pairs`.
    """
    path = Path(path)
    if not path.exists():
        return {}
    by_query: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in read_tsv(path):
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        by_query[key].add((row["TgtEntity"], row["Relation"]))
    return {key: sorted(pairs) for key, pairs in by_query.items()}


def load_graded_relevance(
    path: str | Path,
) -> dict[tuple[str, str], dict[tuple[str, str], float]] | None:
    """
    Graded gains from `graded.tsv` -> `{(Src, QueryID): {(Tgt, Rel): gain}}`, or
    `None` if the file does not exist. These must match what
    `relevance.compute_graded_relevance` would produce for the same query — the
    organiser emits them with that exact function, so consuming the file and
    recomputing in-process give the same H-nDCG.
    """
    path = Path(path)
    if not path.exists():
        return None
    graded: dict[tuple[str, str], dict[tuple[str, str], float]] = defaultdict(dict)
    for row in read_tsv(path):
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        graded[key][(row["TgtEntity"], row["Relation"])] = float(row["Gain"])
    return dict(graded)


##
# BLOCK-FORMAT SUBMISSION
# -----------------------
# the participant submits a flat 4-column TSV (SrcEntity, TgtEntity, Relation,
# Score) with no QueryID — `candidate_count * |relations|` rows per query (the
# default 50 x 3 = 150), one block per answers row IN ORDER. We recover QueryID
# positionally (BIOML-ADR-09) and re-emit the canonical (candidate x relation)
# cartesian product so the metrics see a complete, well-keyed row list.
##


def load_block_format_predictions(
    predictions_path: str | Path,
    answers_path: str | Path,
    relations: list[str] | tuple[str, ...],
    candidate_count: int = 50,
    strict: bool = True,
) -> list[dict[str, str]]:
    """
    Load a block submission into `(SrcEntity, QueryID, TgtEntity, Relation,
    Score)` rows for the scorer, recovering QueryID positionally against
    `answers.tsv`.

    Block `i` (rows `i*block_size : (i+1)*block_size`) maps to answers row `i`.
    Fatal on a row-count mismatch, a `SrcEntity` that disagrees with its block,
    an out-of-vocabulary `Relation`, or an unparseable `Score`. Warns (non-fatal)
    on duplicate `(Tgt, Rel)` pairs (keeps the max) and on canonical pairs missing
    from the block (assigns score 0.0, effective last rank). A `TgtEntity` outside
    the query's candidate pool is silently filtered — the canonical pair it
    displaced then surfaces via the missing-pair warning.
    """
    relations_tuple = tuple(relations)
    relations_set = set(relations_tuple)
    block_size = int(candidate_count) * len(relations_tuple)
    if block_size <= 0:
        raise ValueError(
            f"load_block_format_predictions: block_size = {block_size} "
            f"(candidate_count={candidate_count}, "
            f"relations={list(relations_tuple)})."
        )

    answer_rows = list(read_tsv(answers_path))
    submission_rows = list(read_tsv(predictions_path))
    expected_total = len(answer_rows) * block_size
    if len(submission_rows) != expected_total:
        raise ValueError(
            f"block submission row-count mismatch: got {len(submission_rows)}, "
            f"expected {expected_total} ({len(answer_rows)} queries x "
            f"{block_size} rows/query). Each query needs exactly {block_size} "
            f"rows in the canonical block order."
        )

    if not strict:
        # loose: pass rows through verbatim, only recovering QueryID + SrcEntity
        loose: list[dict[str, str]] = []
        for block_idx, answer_row in enumerate(answer_rows):
            block_src = answer_row["SrcEntity"]
            block_query_id = answer_row.get("QueryID", "Q0")
            start = block_idx * block_size
            for sub_row in submission_rows[start:start + block_size]:
                # `or` (not get-default) coalesces csv.DictReader's None for a
                # short row, which would otherwise crash the downstream ranker.
                loose.append({
                    "SrcEntity": sub_row.get("SrcEntity") or block_src,
                    "QueryID": block_query_id,
                    "TgtEntity": sub_row.get("TgtEntity") or "",
                    "Relation": sub_row.get("Relation") or "",
                    "Score": sub_row.get("Score") or "0",
                })
        return loose

    enriched: list[dict[str, str]] = []
    for block_idx, answer_row in enumerate(answer_rows):
        block_src = answer_row["SrcEntity"]
        block_query_id = answer_row.get("QueryID", "Q0")
        block_candidates = set(parse_list(answer_row.get("TgtCandidates", "[]")))
        start = block_idx * block_size

        # pass 1 — validate each row; index surviving (tgt, rel) -> max score
        block_index: dict[tuple[str, str], float] = {}
        duplicates_seen: set[tuple[str, str]] = set()
        for row_offset, sub_row in enumerate(
            submission_rows[start:start + block_size]
        ):
            row_idx = start + row_offset
            sub_src = sub_row.get("SrcEntity", "")
            if sub_src != block_src:
                raise ValueError(
                    f"block submission row {row_idx}: SrcEntity {sub_src!r} "
                    f"does not match the canonical {block_src!r} for block "
                    f"{block_idx} (per the answers.tsv order). Each block of "
                    f"{block_size} consecutive rows shares one SrcEntity."
                )
            tgt = sub_row.get("TgtEntity", "")
            rel = sub_row.get("Relation", "")
            if rel not in relations_set:
                raise ValueError(
                    f"block submission row {row_idx}: Relation {rel!r} is not "
                    f"in the canonical vocabulary {sorted(relations_set)}."
                )
            try:
                score = float(sub_row.get("Score", ""))
            except (TypeError, ValueError):
                raise ValueError(
                    f"block submission row {row_idx}: Score "
                    f"{sub_row.get('Score', '')!r} is not a float."
                ) from None
            if not math.isfinite(score):
                # nan/inf parse fine but break the deterministic ranking
                raise ValueError(
                    f"block submission row {row_idx}: Score "
                    f"{sub_row.get('Score', '')!r} is not finite."
                )

            if tgt not in block_candidates:
                continue                         # silent-filter non-candidates
            key = (tgt, rel)
            if key in block_index:
                duplicates_seen.add(key)
                block_index[key] = max(block_index[key], score)
            else:
                block_index[key] = score

        if duplicates_seen:
            warnings.warn(
                f"block {block_idx} (SrcEntity {block_src!r}, QueryID "
                f"{block_query_id!r}): {len(duplicates_seen)} duplicate "
                f"(TgtEntity, Relation) pair(s); kept the max score per pair. "
                f"First few: {sorted(duplicates_seen)[:5]}"
                + (" ..." if len(duplicates_seen) > 5 else ""),
                UserWarning,
                stacklevel=2,
            )

        # pass 2 — emit the full (candidate x relation) product; missing -> 0.0
        missing_pairs: list[tuple[str, str]] = []
        for tgt in sorted(block_candidates):
            for rel in relations_tuple:
                key = (tgt, rel)
                if key in block_index:
                    score_val = block_index[key]
                else:
                    score_val = 0.0
                    missing_pairs.append(key)
                enriched.append({
                    "SrcEntity": block_src,
                    "QueryID": block_query_id,
                    "TgtEntity": tgt,
                    "Relation": rel,
                    # round-trippable repr — the metrics layer owns quantization
                    # (_SCORE_DP); the loader must not pre-truncate precision.
                    "Score": repr(score_val),
                })

        if missing_pairs:
            warnings.warn(
                f"block {block_idx} (SrcEntity {block_src!r}, QueryID "
                f"{block_query_id!r}): {len(missing_pairs)} canonical "
                f"(TgtEntity, Relation) pair(s) missing; assigned score 0.0 "
                f"(effective last rank). First few: {missing_pairs[:5]}"
                + (" ..." if len(missing_pairs) > 5 else ""),
                UserWarning,
                stacklevel=2,
            )

    return enriched
