# O3 native reasoner foundation evidence

## Implemented boundary

This commit implements the dependency-independent O3 integration boundary without
claiming that the sibling reasoners are already complete:

- `HermiTReasoner` targets the frozen pyHermiT public `Reasoner`,
  `ReasonerConfig(timeout=...)`, `is_consistent()`,
  `unsatisfiable_classes()`, `InconsistentOntologyError`, and
  `ReasonerTimeoutError` contracts. It retains the exact composite by identity,
  converts both pyHermiT inconsistency forms to classical explosion, and maps only
  the public cooperative timeout exception to the OAEI fallback signal.
- `ELKReasoner` targets the frozen pyELK `Reasoner`, `ReasonerConfig`,
  `ReasoningResult`, and `Taxonomy.bottom` contracts. Unbounded calls may retain
  in-process identity. Every bounded call encodes the composite with the planned
  core wire API and sends only bytes plus three fingerprints to a spawn worker.
  The child verifies the decoded fingerprints, records zero OWL parser calls, and
  returns a bounded JSON result. Timeout terminates the process; malformed output,
  crashes, unverified wire, or any nonzero parse count are errors.
- The native dispatcher is now the default shared-view backend. ROBOT/DeepOnto
  remain explicitly selected, quarantined differential code until O4; no native
  failure silently selects them.
- HermiT timeout fallback passes the exact same composite object to ELK. A built-in
  `TimeoutError`, resource/backend/profile failure, worker failure, or any other
  exception does not trigger fallback. The recorded elapsed time covers both the
  timed-out HermiT attempt and ELK fallback.
- `UnsatResult` carries additive native provenance, inconsistency, and fallback
  fields. Official shared reports add a deterministic
  `coherence-provenance/1` block covering package/methodology versions, safe
  execution options, core API/model/wire versions, composite/source/target
  fingerprints and roles, hashed document/import manifests, loader diagnostics,
  bridge normalization counts/fingerprint, requested/actual reasoner and backend,
  profile reasons, transport mode, timeout/fallback/lower-bound/inconsistency, and
  denominator/numerator counts and sorted-line digests. Paths, credentials,
  object IDs, and timestamps are excluded.

The historical metric meaning and keys are unchanged. Inconsistent views produce
the complete named-class denominator as the numerator, so global incoherence is
`1.0`; ELK results remain labeled lower bounds.

## Differential harnesses

Two Java-free manual gates are staged:

- `tools/native_fixture_compare.py` runs both native adapters over all five pinned
  clean, equivalence, subsumption-direction, and already-incoherent fixtures and
  requires exact reasoner identity, denominator, numerator count, and sorted IRI
  digest agreement.
- `tools/native_compare.py` verifies the external public NCIT-DOID ontology and
  1,406-alignment hashes before requiring exact agreement with the frozen 24,227
  named-class / 2,227 unsatisfiable-class real-data evidence.

Ordinary tests validate every comparator branch against the immutable baselines,
but do not relabel those protocol tests as native semantic runs.

## Current dependency block and deliberately deferred evidence

The real semantic comparison cannot yet run:

- pyHermiT `7471bf9` exposes configuration/contracts/normalization/role work but
  does not yet export the planned WP14/WP16 `Reasoner` classification facade;
- pyELK `e37ce19` exposes shared input/index/raw-result contracts but explicitly
  defers its public `Reasoner` to WP10; and
- pyowl-core `ad3b0dc` has composition/indexes but not the planned WP06
  `encode_snapshot`/`decode_snapshot` wire API.

The adapters issue explicit missing-package, missing-export, incompatible-version,
and missing-wire errors at those boundaries. Therefore this work does **not** mark
O3 semantic acceptance complete, does not claim fixture or NCIT-DOID native
agreement, and does not report reasoner compile/classification performance. Those
gates must be rerun after the sibling public facades and core wire land. No API was
invented to bypass them; no GPU, OpenRouter, Java runtime fallback, or private OWL
model was used.

## Verified gates

The deterministic public-protocol tests cover:

- exact ontology identity and timeout configuration at pyHermiT construction;
- sorted unsatisfiable extraction and `owl:Nothing` exclusion;
- both inconsistent-ontology paths for pyHermiT and pyELK;
- HermiT-timeout-only fallback, same-composite identity, and failure propagation;
- bounded worker success, crash/EOF, malformed capability, termination, verified
  wire, and zero-OWL-parse enforcement;
- stable bridge/provenance counts and digests with no local identity/path leakage;
- every small pinned fixture/backend comparator case and the public NCIT-DOID
  comparator contract; and
- concrete core composite role/fingerprint/document provenance on Python 3.10 and
  3.12.

The full Java-free suite passes 167 tests with 11 expected optional skips on
Python 3.10 and 163 tests with 12 expected optional skips on Python 3.12 (that
environment does not discover the sibling core unless explicitly placed on
`PYTHONPATH`). With the sibling core explicit, all five O1-O3 concrete
identity/provenance checks pass on both versions. The unchanged
`metric-contract/1`, Ruff, strict mypy for all O1-O3 shared modules, compileall,
diff checks, and the native-source Java/pickle/subprocess scan also pass.

## Foundation performance observations

`benchmarks/bench_o3_foundation.py` separates bridge normalization, zero-copy
composition, signature indexing, and provenance. With 5,000 equivalence mappings
(10,000 named classes) on the local runner:

| Python | normalize | compose | signature | cold provenance | warm provenance | peak process RSS |
|---|---:|---:|---:|---:|---:|---:|
| 3.12.3 | 0.007 s | 1.701 s | 0.983 s | 2.144 s | 0.012 s | 41.5 MB |
| 3.10.11 | 0.010 s | 1.976 s | 1.136 s | 2.565 s | 0.012 s | 38.2 MB |

Both runs retained source/target identity, contained exactly 5,000 bridge delta
entries, and emitted a roughly 3.7 KB provenance record. Cold provenance includes
the required first composite structural/logical/signature fingerprint calculation;
the cached repeat is the relevant reporting overhead after reasoner compilation.

A five-run test-protocol spawn/JSON worker probe had median launch/round-trip
overhead of 0.247 s on Python 3.12 and 0.428 s on Python 3.10. This is process-boundary
evidence only: core wire bytes and native pyELK classification are still deferred.

## Reproduction

```text
PYTHONPATH=src python3.10 -m unittest discover -s tests
PYTHONPATH=src python3.12 -m unittest discover -s tests
PYTHONPATH=src:../pyOWLCore/src python -m unittest \
  tests.test_coherence.TestConcreteCoreIdentity \
  tests.test_native_reasoners.TestConcreteCoreProvenance -v
PYTHONPATH=src python tools/metric_contract.py
PYTHONPATH=src:../pyOWLCore/src python benchmarks/bench_o3_foundation.py \
  --bridge-count 5000
ruff check src/oaei_bioml_eval/coherence tests/test_coherence.py \
  tests/test_native_reasoners.py tools/native_compare.py \
  tools/native_fixture_compare.py tools/robot_oracle.py
```
