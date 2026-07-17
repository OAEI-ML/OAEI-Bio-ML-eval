"""
oaei_bioml_eval.typed.cli: the `score` command for the Track 2 typed scorer.

A thin wrapper over `report.score_files` so a participant can self-score a block
submission from the shell and get the same numbers the leaderboard reports:

    python -m oaei_bioml_eval.typed.cli predictions.tsv answers.tsv \
        --hierarchy hierarchy.tsv --output scores.json

Prints the metric dict as JSON to stdout (and writes it when `--output` is set).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .metrics import DEFAULT_RELATIONS
from .relevance import DEFAULT_MAX_DISTANCE
from .report import score_files


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oaei-bioml-typed-score",
        description="score a Track 2 typed-ranking submission.",
    )
    parser.add_argument("predictions", help="the submission TSV")
    parser.add_argument("answers", help="the per-query gold answers.tsv")
    parser.add_argument(
        "--format", choices=("block", "row"), default="block",
        dest="submission_format", help="submission layout (default: block)",
    )
    parser.add_argument("--preferred", dest="preferred_pairs_path",
                        help="preferred.tsv; else derived from the answers")
    parser.add_argument("--graded", dest="graded_relevance_path",
                        help="graded.tsv of H-nDCG gains")
    parser.add_argument("--hierarchy", dest="hierarchy_path",
                        help="hierarchy.tsv; recomputes H-nDCG gains in-process")
    parser.add_argument("--output", dest="output_path",
                        help="write the metric dict here as JSON")
    parser.add_argument("--candidate-count", type=int, default=50,
                        help="candidates per query for block parsing (50)")
    parser.add_argument("--k", type=int, default=10, help="nDCG/Hits cutoff (10)")
    parser.add_argument("--max-distance", type=int, default=DEFAULT_MAX_DISTANCE,
                        help=f"graded-gain horizon (default {DEFAULT_MAX_DISTANCE})")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    metrics = score_files(
        args.predictions, args.answers,
        output_path=args.output_path,
        preferred_pairs_path=args.preferred_pairs_path,
        graded_relevance_path=args.graded_relevance_path,
        hierarchy_path=args.hierarchy_path,
        submission_format=args.submission_format,
        relations=DEFAULT_RELATIONS,
        candidate_count=args.candidate_count,
        k=args.k,
        max_distance=args.max_distance,
    )
    json.dump(metrics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
