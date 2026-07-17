# O0 metric freeze and Java-migration audit

## Completed evidence

- `metric-contract/1` freezes input hashes and full typed/equivalence numeric
  dictionaries. It compares floats at the project's stated 12-decimal metric
  precision, eliminating a one-ULP `math.log2` difference observed between CPython
  3.10 and 3.12 without altering ranking or scoring code.
- The quarantined ROBOT 1.9.10 oracle is pinned by release URL and
  SHA-256 `16a73c074f3df359a7338a84b4e0788785fe06117f931bb9796e9619ea776105`.
  Two consecutive Java 11 runs produced identical semantic JSON for clean,
  equivalence-clash, both subsumption directions, and already-incoherent fixtures.
- Normal Python 3.10 and 3.12 source suites each pass 134 tests with 11 expected
  optional-dependency/JVM skips. `compileall` passes for `src`, `tools`, and `tests`.
- The public pinned NCIT–DOID corpus is available outside Git. With no alignment
  bridge, ROBOT merged it in 25.20 s, enumerated 24,227 named classes in 26.49 s,
  and found no unsatisfiable class with HermiT (52.75 s) or ELK (24.68 s).
- With all 1,406 NCIT–DOID training equivalences applied, the merged ontology still
  contained 24,227 named classes. HermiT found 2,227 unsatisfiable classes in
  722.34 s; ELK found the identical sorted set in 22.47 s. Both sets have SHA-256
  `8dd56db2f864e757fb9fe04ca9b4cb6798e161597ff715f81175129db8bc27ab`.
  The source, target, bridge, tool, result, and timing evidence is frozen in
  `tests/baselines/robot-1.9.10-ncit-doid-train.json`. This validates semantic
  agreement on the available EL-profile workload while quantifying why native
  task-specific dispatch and explicit timeout handling are required.

## Removal inventory

The following are intentionally still present until O1–O4 provide and verify the
native replacement:

- `coherence/reasoner.py`: `RobotReasoner`, `DeepOntoReasoner`, `MergedOntology`,
  subprocess/process-group control, functional bridge files, SPARQL/log parsing,
  JPype/OWLAPI imports, JVM heap/environment handling, and Java backend selection;
- `coherence/cli.py`: `--backend`, `--robot-jar`, ROBOT/DeepOnto help, and the old
  file-only source/target interface;
- `pyproject.toml`: the `reasoner` RDFLib extra and `deeponto` extra; and
- skip-guarded runtime tests for ROBOT and DeepOnto.

O4 deletes those surfaces only after shared snapshots, composite bridges, native
reasoner adapters, timeout behavior, and differential baselines pass. The oracle tool
and committed provenance remain repository-only and are excluded from ordinary CI.

## Compatibility and quality audit

- Current metadata remains `0.1.0.dev0` / Python `>=3.12` by design. The coordinated
  `0.2.0` / `>=3.10` change is an O5 release gate, not an early claim.
- Runtime base dependencies are empty. RDFLib, ROBOT, and DeepOnto are not imported by
  typed/equivalence scoring. The final reasoner extra must become compatible
  `pyowl-core`/pyHermiT/pyELK ranges and the DeepOnto extra must disappear.
- Python 3.10 syntax and stdlib compatibility pass today when run from source.
- Pre-existing strict-quality debt is recorded rather than hidden: Ruff reports the
  lambda assignment in `equivalence/report.py` and one unused test import; strict mypy
  reports 51 errors, concentrated in legacy loader/coherence typing. O1–O5 must reduce
  these to zero before release.
- The root module docstring still describes a DeepOnto-backed planned surface and all
  CLI/API/reference documentation still describes the old runtime. O4/O5 own the
  replacement documentation and migration errors.

## Reproduction

Java-free checks:

```text
PYTHONPATH=src python3.10 -m unittest discover -s tests
PYTHONPATH=src python3.12 -m unittest discover -s tests
PYTHONPATH=src python tools/metric_contract.py
```

The manual ROBOT workflow is `.github/workflows/oracle.yml`; it verifies the JAR
digest before executing the two-pass semantic comparison.
