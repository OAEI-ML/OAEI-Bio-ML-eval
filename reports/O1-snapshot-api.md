# O1 shared-view API evidence

## Implemented contract

- `score_global_coherence`, `score_reference_coherence`, and
  `score_local_coherence` are snapshot-first public entry points. They hand the
  exact source and target objects to a shared-view reasoner seam and never call
  the core coercion facade themselves.
- The three existing `*_files` conveniences accept the complete core input
  surface without pre-reading paths or streams. When a shared-view adapter is
  active, each invokes `pyowl_core.coerce_snapshot` exactly once for source and
  once for target, forwards document IRIs, load options, resolver, and
  cancellation token, and reuses the returned identities.
- Instrumented tests record source/target identity, view-handoff count, core
  coercion count, and `SnapshotProvider.owl_snapshot()` count. They also prove a
  core loader exception is propagated by identity without a retry.
- Global/local arithmetic, numerator-subset validation, the HermiT-timeout gate,
  query-occurrence weighting, and the existing selectable micro/macro task
  aggregation are unchanged. The frozen `metric-contract/1` remains byte-identical.

## Deliberate transition boundary

`RobotReasoner` and `DeepOntoReasoner` remain path-only differential oracles and
do not opt into `accepts_ontology_views`. This keeps the O0 comparison path
available without pretending that a Java reparse satisfies the shared-view
contract. The remaining work is intentionally not pulled into O1:

- O2 owns `OntologyDelta` bridge construction, zero-copy `compose_views`, and
  deletion of bridge/merged serialization from the official path.
- O3 owns the pyHermiT/pyELK adapters, worker wire protocol, timeout enforcement,
  consistency handling, and full provenance.
- O4 removes the Java runtime surfaces after differential verification.
- O5 owns dependency metadata, the version/Python-requirement bump, release docs,
  packaging, and the full quality/performance matrix.

## Reproduction

```text
PYTHONPATH=src python3.10 -m unittest discover -s tests -v
PYTHONPATH=src python3.12 -m unittest discover -s tests -v
PYTHONPATH=src:../pyOWLCore/src python -m unittest \
  tests.test_coherence.TestConcreteCoreIdentity -v
PYTHONPATH=src python tools/metric_contract.py
python -m compileall -q src tools tests
ruff check src/oaei_bioml_eval/coherence tests/test_coherence.py
```

Both Python suites pass 139 tests with 11 expected optional RDF/JVM skips. The
two sibling-core integration checks additionally prove concrete
`OntologySnapshot` identity, exactly-once stream coercion, and caller stream
ownership on Python 3.10 and 3.12. The modified snapshot report module also
passes strict mypy in isolation; the repository-wide pre-existing
loader/reasoner typing debt remains assigned to the later migration packages
recorded in the O0 audit.
