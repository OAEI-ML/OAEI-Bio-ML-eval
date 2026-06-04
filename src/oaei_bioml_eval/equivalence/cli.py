"""
oaei_bioml_eval.equivalence.cli: the `score` command for Track 1.

    oaei-bioml-equivalence-score global submission.rdf reference.tsv
    oaei-bioml-equivalence-score local  ranking.tsv  gold.tsv --candidate-count 50

Note: we can modify the `--candidate-count` flag to be 100 for synchronisation with
preivous years; or we can continue to tune it specifically for this release (todo: 
review & revisit).

Prints the metric dict as JSON to stdout (and writes it when `--output` is set).
The global subcommand needs the `[rdf]` extra only when the submission is RDF.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .report import score_global_files, score_local_files


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oaei-bioml-equivalence-score",
        description="score a Track 1 equivalence submission (global P/R/F1 or local MRR/Hits@k).",
    )
    sub = parser.add_subparsers(dest="subtask", required=True)

    p_global = sub.add_parser("global", help="global alignment -> Precision/Recall/F1")
    p_global.add_argument("submission", help="alignment RDF or DeepOnto TSV")
    p_global.add_argument("reference", help="the complete reference TSV (SrcEntity, TgtEntity)")
    p_global.add_argument("--output", dest="output_path", help="write the metric dict here as JSON")

    p_local = sub.add_parser("local", help="local ranking -> MRR + Hits@{1,5,10}")
    p_local.add_argument("submission", help="ranking submission (TgtCandidates list or scored block)")
    p_local.add_argument("gold", help="per-query gold TSV (SrcEntity, TgtEntity)")
    p_local.add_argument("--candidate-count", type=int, default=50,
                         help="candidates per query for block parsing (50)")
    p_local.add_argument("--output", dest="output_path", help="write the metric dict here as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.subtask == "global":
        metrics = score_global_files(args.submission, args.reference, output_path=args.output_path)
    else:
        metrics = score_local_files(
            args.submission, args.gold,
            candidate_count=args.candidate_count, 
            output_path=args.output_path
        )
    json.dump(metrics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
