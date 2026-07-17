# O3 native reasoner integration evidence

## Completed boundary

O3 now runs against the concrete public sibling contracts rather than protocol
doubles alone. The verified local revisions are:

- pyowl-core `86a73d116445e101ae196005ab50d802b451281f`;
- pyHermiT `de061742118180594badf040206edf09822152ce`; and
- pyELK `c076c7e5a2784c46e737a32446c22d82fdee3d4d`.

`HermiTReasoner` retains the exact `OntologyComposite`, constructs the public
`pyhermit.Reasoner` with a cooperative timeout, establishes consistency before
querying unsatisfiable classes, converts both inconsistent-ontology result forms
to classical explosion, and disposes the session. Only the public
`ReasonerTimeoutError` becomes the OAEI HermiT-timeout fallback signal.

`ELKReasoner` retains identity for unbounded calls. Every bounded call encodes the
same composite as PYOCORE wire, sends bytes and authenticated semantic
fingerprints to a spawn worker, verifies all three fingerprints after decode, and
requires a zero-OWL-parse result. The worker is terminated on timeout and cannot
receive an ontology path or pickle. The emitted wire header version, rather than
the core decoder's maximum supported minor, is recorded in provenance.

The adapter now resolves the real pyELK distribution name
`pyelk-reasoner`. This was a concrete integration blocker hidden by the earlier
protocol doubles, because the import package is `pyelk`. Version checking remains
on the compatible `>=0.1,<0.2` line.

Fallback retains the same composite identity and now preserves a structured
record of the timed-out HermiT package, backend, transport, and elapsed time in
`coherence-provenance/1.prior_attempts`. Other timeout classes, backend failures,
profile failures, resource failures, and worker failures still propagate without
fallback.

No O3 runtime path imports or invokes Java, ROBOT, DeepOnto, JPype, OWLAPI,
Exact-OM, or the projector. The typed/equivalence metric modules remain outside
the reasoner dependency graph.

## Semantic acceptance

The concrete pyHermiT and pyELK facades both match every frozen ROBOT 1.9.10
small fixture exactly:

| Fixture | Denominator | HermiT unsatisfiable | ELK unsatisfiable |
|---|---:|---:|---:|
| already incoherent | 5 | 1 | 1 |
| equivalence clash | 4 | 2 | 2 |
| equivalence clean | 4 | 0 | 0 |
| forward subsumption clash | 4 | 1 | 1 |
| reverse subsumption clash | 4 | 1 | 1 |

The gate compares the sorted IRI digest as well as counts and reasoner identity.
It passes on CPython 3.10 and 3.12 for:

- pyHermiT pure Python, in-process identity;
- pyELK pure Python, in-process identity;
- pyELK pure Python, bounded verified-wire worker; and
- a locally built pyELK 0.1 Rust artifact, bounded verified-wire worker.

Concrete inconsistent composites containing a named class plus an individual
asserted to `owl:Nothing` produce the full two-class denominator as the numerator,
`global_coherence == 1.0`, and `inconsistent: true` from both adapters.
Concrete pyHermiT also proves its public cooperative timeout maps to the OAEI
fallback signal with attempt provenance. Protocol tests separately prove that
the same composite reaches ELK, while built-in `TimeoutError` and ordinary
failures do not fall back.

## Verification matrix

With all four source trees on `PYTHONPATH` and both reasoners forced to their
compiler-free Python backends:

| Gate | CPython 3.10.11 | CPython 3.12.3 |
|---|---:|---:|
| full Java-free unittest suite | 176 passed, 11 optional skips | 176 passed, 11 optional skips |
| HermiT five-fixture exact comparator | pass | pass |
| ELK in-process five-fixture comparator | pass | pass |
| ELK bounded-wire five-fixture comparator | pass | pass |
| ELK Rust-artifact bounded comparator | pass | pass |
| `metric-contract/1` | pass | pass |
| compileall (`src`, `tools`, `tests`, `benchmarks`) | pass | pass |

Ruff passes the complete coherence/O3 test, tool, and benchmark surface. Strict
mypy passes `bridge.py`, `native_reasoners.py`, `provenance.py`, `report.py`, the
concrete integration tests, and the native benchmark. Static import/call scans of
the new native adapter, worker, bridge, provenance, and benchmark paths find no
Java/ROBOT/DeepOnto/JPype/OWLAPI, pickle, subprocess, projector, or Exact coupling.

## Performance evidence

`benchmarks/bench_o3_native.py` now separates source load, target load, bridge
normalization, zero-copy composition, signature indexing, core-wire encoding,
reasoner compilation, consistency, classification, and reporting. It records
stage wall time, peak-RSS increments, wire bytes, backend metadata, semantic
counts, and provenance size.

On the local runner, a generated EL fixture with 400 named classes, 100 bridge
equivalences, and 200 expected unsatisfiable classes produced:

| Backend | Python | compile | consistency | classification | full reasoning | peak process RSS |
|---|---:|---:|---:|---:|---:|---:|
| pyELK Python | 3.10 | 0.687 s | 0.003 s | 2.022 s | 2.753 s | 38.3 MB |
| pyELK Python | 3.12 | 0.585 s | 0.003 s | 1.224 s | 1.848 s | 42.0 MB |

The Python 3.12 run additionally measured source/target load at 0.195/0.054 s,
composition at 0.062 s, signature indexing at 0.108 s, a 204,988-byte wire image
at 0.453 s, and provenance reporting at 0.0014 s. A locally built pyELK 0.1 Rust
artifact also returned the exact 200-class numerator; on this small workload its
worker and scheduling overhead made it slower than Python, so this result is not
presented as a native speedup claim.

The same benchmark exposes a current upstream scale risk rather than hiding it:
pure pyHermiT required 5.59 s on Python 3.10 and 4.22 s on Python 3.12 to reason
over only 10 named classes and two bridge equivalences. O3 integration adds no
per-axiom conversion or reparsing, but it cannot make the sibling tableau/classifier
faster. Large-DL performance therefore remains a release gate for pyHermiT/native
work and O5, not accepted performance evidence for this evaluator.

## External real-data gate

`tools/native_compare.py` is ready for the pinned public NCIT-DOID source, target,
and 1,406-row alignment. It verifies all three input hashes before comparing the
24,227-class denominator and exact 2,227-class numerator digest. Those external
files are not present in this workspace, so this commit does not fabricate an
NCIT-DOID rerun or claim the licensed SNOMED-scale gate. The immutable comparator
contract remains covered by ordinary tests. Supplying the pinned files is the only
remaining input requirement for that manual O3/O5 evidence run.

## Reproduction

```text
PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYHERMIT_BACKEND=python PYELK_PURE_PYTHON=1 \
  python3.10 -m unittest discover -s tests
PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYHERMIT_BACKEND=python PYELK_PURE_PYTHON=1 \
  python3.12 -m unittest discover -s tests

PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYHERMIT_BACKEND=python python tools/native_fixture_compare.py \
  --reasoner hermit --timeout none
PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYELK_PURE_PYTHON=1 python tools/native_fixture_compare.py \
  --reasoner elk --timeout 60

PYTHONPATH=src python tools/metric_contract.py
python -m compileall -q src tools tests benchmarks
ruff check src/oaei_bioml_eval/coherence tests/test_coherence.py \
  tests/test_native_reasoners.py tests/test_native_reasoner_integration.py \
  tools/native_compare.py tools/native_fixture_compare.py tools/robot_oracle.py \
  benchmarks/bench_o3_foundation.py benchmarks/bench_o3_native.py
```
