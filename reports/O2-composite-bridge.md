# O2 zero-copy composite and bridge evidence

## Implemented contract

- The official shared path now creates OWL bridge axioms only with the public
  `pyowl_core` model: `EquivalentClasses` for `=`/`equivalent`/`equiv`, forward
  `SubClassOf` for `<`/`<=`, and reverse `SubClassOf` for `>`/`>=`.
- Correspondences are normalized to one sorted tuple, duplicates and self-pairs
  are removed, unsupported relations fail before composition, and repaired
  reference `asserted_correspondences` reports the normalized asserted count.
- One `OntologyDelta(add_axioms=...)` is passed to
  `compose_views(source, target, delta=..., roles=("source", "target"))`.
  The resulting `OntologyComposite` retains both input views by identity. Its
  shared signature supplies the denominator and the exact same object reaches
  both the preferred reasoner pass and HermiT-timeout fallback.
- The shared path neither calls `materialize()` nor writes any bridge/merged
  artifact. Functional bridge and merged-file creation were removed from the
  installable `src/` tree; the old serializer now exists only inside the manual
  `tools/robot_oracle.py` differential harness.
- Typed/equivalence behavior and selectable micro/macro aggregation were not
  changed. `metric-contract/1` remains byte-identical.

## Identity, no-copy, and scale evidence

Concrete sibling-core tests verify:

- `coerce_snapshot(composite) is composite`;
- source/target member identity and roles are retained;
- snapshot and overlay bases remain unchanged, including original axiom object
  identities;
- exact canonical bridge axiom classes and subsumption directions;
- no `Path.write_text` call and no `OntologyComposite.materialize()` call;
- one composite is reused across HermiT-timeout fallback; and
- stream wrappers still coerce once per ontology and leave caller streams open.

An untraced local scale probe constructed 5,000 distinct bridge axioms in
2.36 seconds on Python 3.12 and 2.44 seconds on Python 3.10. Both results retained
the two source arenas by identity and contained exactly 5,000 delta entries. A
separate `tracemalloc` probe grew from 860,251 peak bytes for 1,000 entries to
1,359,260 bytes for 2,000 entries (1.58x peak for 2x bridge size); these are
development observations, not the pinned O5 release benchmark.

## Reproduction

```text
PYTHONPATH=src python3.10 -m unittest discover -s tests
PYTHONPATH=src python3.12 -m unittest discover -s tests
PYTHONPATH=src:../pyOWLCore/src python -m unittest \
  tests.test_coherence.TestConcreteCoreIdentity -v
PYTHONPATH=src python tools/metric_contract.py
python -m compileall -q src tools tests
ruff check src/oaei_bioml_eval/coherence tests/test_coherence.py tools/robot_oracle.py
```

The Java-free base suite passes 141 tests on each Python version with 11 expected
optional RDF/JVM skips. All four concrete core checks pass on Python 3.10 and
3.12. The modified bridge/report modules pass strict mypy in isolation, and the
installable source scan contains no bridge `.ofn`, merged-file path, or
`write_bridge_ofn` symbol.

## Deliberate transition boundary

O2 does not implement classification. O3 must provide pyHermiT/pyELK adapters,
wire-only ELK workers, consistency handling, and full provenance. The old
ROBOT/DeepOnto classifier classes, CLI flags, environment variables, and skipped
JVM tests remain until O4 deletes those Java surfaces after native differential
verification. No O3 or O4 behavior is claimed here.
