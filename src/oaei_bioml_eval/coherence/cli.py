"""
oaei_bioml_eval.coherence.cli: the organiser-computed coherence command.

    oaei-bioml-coherence-score global       submission.rdf src.owl tgt.owl  --reasoner hermit
    oaei-bioml-coherence-score local        ranked.tsv     src.owl tgt.owl  --reasoner hermit
    oaei-bioml-coherence-score structural   submission.rdf                  (STUB — open rule set)

Reports a degree of INCOHERENCE in [0, 1] (0 = clean, higher = worse). Official
scoring uses the native shared-view reasoner stack. Prints the metric dictionary
as JSON to stdout (and writes it when ``--output`` is set).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .report import (
    score_global_coherence_files,
    score_local_coherence_files,
    score_structural_proxy_files,
)

_REMOVED_OPTIONS = frozenset(
    {"--backend", "--robot-jar", "--java", "--robot-cmd", "--heap"}
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oaei-bioml-coherence-score",
        description="organiser-computed Track 1 alignment coherence (degree of INCOHERENCE; 0 = clean).",
    )
    sub = parser.add_subparsers(dest="kind", required=True)

    for kind, sub_help, first in (("global", "global alignment -> unsatisfiable count + degree", "submission RDF/TSV"),
                                  ("local", "rank-1 committed mappings -> mean per-query incoherence", "local.test.ranked.tsv")):
        p = sub.add_parser(kind, help=sub_help)
        p.add_argument("submission", help=first)
        p.add_argument("src_owl", help="source ontology OWL")
        p.add_argument("tgt_owl", help="target ontology OWL")
        p.add_argument("--reasoner", choices=("hermit", "elk"), default="hermit", help="HermiT exact (default) or ELK (`>=`)")
        p.add_argument("--timeout", dest="timeout_s", type=float, default=7200.0, help="HermiT wall-clock gate seconds -> ELK (7200)")
        p.add_argument("--skip-invalid-iris", dest="skip_invalid", action="store_true",
                       help="drop correspondences whose IRIs are malformed (embedded whitespace) instead of erroring")
        p.add_argument("--output", dest="output_path", help="write the metric dict here as JSON")

    p_struct = sub.add_parser("structural", help="dependency-free structural proxy (STUB — open rule set)")
    p_struct.add_argument("submission", help="submission RDF/TSV")
    p_struct.add_argument("--output", dest="output_path", help="write the metric dict here as JSON")
    return parser


def _reject_removed_options(
    parser: argparse.ArgumentParser, argv: Sequence[str]
) -> None:
    for argument in argv:
        option = argument.split("=", 1)[0]
        if option in _REMOVED_OPTIONS:
            parser.error(
                f"{option} was removed in 0.2.0; use --reasoner hermit or elk"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    _reject_removed_options(parser, arguments)
    args = parser.parse_args(arguments)
    if args.kind == "global":
        metrics = score_global_coherence_files(
            args.submission, args.src_owl, args.tgt_owl, reasoner=args.reasoner,
            timeout_s=args.timeout_s,
            skip_invalid=args.skip_invalid, output_path=args.output_path
        )
    elif args.kind == "local":
        metrics = score_local_coherence_files(
            args.submission, args.src_owl, args.tgt_owl, reasoner=args.reasoner,
            timeout_s=args.timeout_s,
            skip_invalid=args.skip_invalid, output_path=args.output_path
        )
    else:
        metrics = score_structural_proxy_files(args.submission, output_path=args.output_path)
    json.dump(metrics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
