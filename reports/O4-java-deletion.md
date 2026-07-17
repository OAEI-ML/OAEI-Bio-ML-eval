# O4 Java runtime deletion

## Outcome

The installable evaluator now has one coherence path: both file/stream entry
points coerce source and target once through `pyowl-core`, compose one shared
view, and dispatch that same view to native pyHermiT or pyELK. The package no
longer contains file-merge reasoner handles, ROBOT/DeepOnto implementations,
subprocess log parsing, backend selection, discovery environment variables, or
JVM-gated coherence tests.

The existing typed and equivalence metric arithmetic and file formats were not
changed. References to a vendor-specific TSV format were renamed to the neutral
term “alignment TSV”.

## Public migration

- `CoherenceReasoner` now exposes only `unsatisfiable_classes_view`.
- `load_reasoner()` always returns the lazy native dispatcher.
- `MergedOntology`, merge/dispose methods, and installable Java adapters are
  deleted.
- The coherence CLI keeps `--reasoner`, `--timeout`, `--output`, and
  `--skip-invalid-iris`.
- Removed process/backend flags are rejected before ordinary argument parsing
  with a concise `removed in 0.2.0` migration error.
- The obsolete backend environment variable has no runtime effect.
- The `deeponto` extra and external process prerequisites are gone. O5 owns the
  final native dependency ranges and the coordinated version/Python bump.

## Quarantine boundary

ROBOT 1.9.10 remains solely as historical development-oracle provenance:

- committed read-only JSON baselines;
- `.github/workflows/oracle.yml`, an explicitly manual workflow; and
- `tools/robot_oracle.py`, a self-contained repository tool that is not shipped
  as a package module and is never imported by ordinary tests.

The oracle tool retains its own temporary-file/process implementation so no
legacy class is re-exported from `src/oaei_bioml_eval`.

## Verification

All commands used sibling source trees and forced their pure/native development
backends (`PYHERMIT_BACKEND=python`, `PYELK_PURE_PYTHON=1`).

- Python 3.10: `unittest discover -s tests -v` — 172 run, 166 passed,
  6 skipped.
- Python 3.12: `unittest discover -s tests -v` — 172 run, 166 passed,
  6 skipped.
- The six skips are RDFLib-extra tests only; there are no Java/JVM/ROBOT or
  DeepOnto skip gates.
- Strict mypy passed for the narrowed reasoner contract, report, CLI, package
  exports, and new static guardrail test.
- Ruff passed for every O4-modified Python file.
- `compileall` passed for `src`, `tools`, and `tests`.
- The runtime AST guard found no installable imports of `subprocess`, `deeponto`,
  `jpype`, `java`, or `org`.
- Metadata/source scans found no legacy reasoner types, backend environment
  selector, ROBOT JAR selector, or DeepOnto extra.
- Native CLI help contains no Java installation or discovery instructions; the
  quarantined oracle CLI remains separately importable.
- `git diff --check` passed.

No Java executable or oracle regeneration was needed for these gates.
