# O3 native reasoner integration evidence

## Completed boundary

O3 now runs against the concrete public sibling contracts rather than protocol
doubles alone. The verified local revisions are:

- pyowl-core `354da9a`;
- pyHermiT `d6196d7`; and
- pyELK `1a05d16`, including cleared worker-local saturation workspace reuse.

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
- a locally built pyHermiT ABI3 native artifact, in-process identity;
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
| installed-native refresh (`d6196d7` / `1a05d16`) | 178 passed, 6 optional skips, 24 subtests | 178 passed, 6 optional skips, 24 subtests |
| HermiT five-fixture exact comparator | pass | pass |
| HermiT native-ABI3 five-fixture comparator | pass | pass |
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

## External real-data gate — semantic acceptance captured, performance blocked

The pinned NCIT–DOID source, target, and 1,406-row training alignment are locally available and
match the frozen hashes:

- source SHA-256 `379a37f47c0c8e7c30397769358cca955140d16b2797a1cc75da4b1fc2b354eb`;
- target SHA-256 `76f41cce3616ad1a9ba6353f469e96bde7addba5d43e541651a3ab703f9ba2bc`;
  and
- alignment SHA-256 `9e417056a39996575c4409b4ea89b01dc5cc1f8962c9123975fc6594a3e089b9`.

The current schema-2 capture is persisted as `reports/O3-ncit-doid-schema2.json` (SHA-256
`23ee9f77cc8fb24e4b2652173f5e4b87e25b36aa4d867810113d283459e917b7`). It used the comparator
at `7f80aa8`, pyowl-core `354da9a`, and an installed pyELK wheel built from `1a05d16`. The report
binds all three input buffers and the frozen ROBOT 1.9.10 baseline, whose SHA-256 is
`957831a1654c8151bbbd012cce0d0ff535622b3ad64141a84ed5109e5fc2b1a2`.

The run returned 24,227 named classes, 1,406 correspondences, and exactly 2,227 unsatisfiable
classes. Its sorted-numerator SHA-256 was
`8dd56db2f864e757fb9fe04ca9b4cb6798e161597ff715f81175129db8bc27ab`, byte-for-byte equal to
the frozen Java ELK oracle, and the ontology remained consistent. The comparator verified the
Rust backend with 12 effective workers and hashed the loaded 1,741,904-byte `_native.abi3.so`
as `5774b58598dbb7fbbd618b0bfc9c848983421292a6627ed7f5b423b7340b2990`. Standalone RDF/XML
loading used pyowl-core's complete Python fallback because this wheel does not advertise the
native RDF/XML parsing capability; this is Java-free but leaves parsing optimization open.

The bounded worker completed in 1,162.032 s, the driver reached pre-serialization in 1,163.178 s,
and pyELK reported 881.863 s of reasoner time. These values fail performance parity with the
22.47 s recorded Java oracle, so the capture closes the NCIT semantic and durable-evidence gates,
not the NCIT performance gate. No peak-RSS claim is made because the platform denied the outer
measurement wrapper's post-child `sysctl` query; the comparator result itself completed and its
create-only JSON was independently hash-validated.

The result profile lists `DATATYPE`, `DATATYPE_DEFINITION`, and
`OBJECT_UNION_OF_POSITIVE` as unsupported. pyELK therefore does not claim formal completeness
for every feature in the ontology; its conservative lower bound nevertheless exactly matches
the 2,227-class Java ELK result for this pinned task.

### Historical unarchived observation

An earlier unbounded Java-free Python/Rust terminal run using pyELK `e9a0892` reported the same
semantic tuple and digest. Operator notes recorded 1,246.278 s of native reasoner time and
1,606.30 s wall, 1,870.04 s user CPU, and 33.64 s system CPU. It did not retain JSON and remains
unverified; the current schema-2 capture above supersedes it for semantic and provenance evidence.

### Superseded blocked attempts

The files became available locally on 2026-07-18 and match all three frozen hashes. The first
attempt exposed a stale comparator call that still passed the removed `backend="native"`
keyword; that repository-owned defect is fixed and covered by a test which invokes the current
file API and rejects reintroduction of the removed option.

A second Java-free attempt requested ELK with a 600-second bound using pyowl-core `47a6695`,
pyELK `2411c10`, and its available Rust backend. It was stopped after exceeding the intended
bounded window without reaching composition or either reasoner. The interrupt traceback was
still inside construction of the **source** `OntologySnapshot`, specifically signature gathering
and structural-node canonical equality. Consequently:

- no ontology was parsed twice and no Java path ran;
- no native unsatisfiable set, count, digest, or semantic agreement is claimed;
- the reasoner timeout could not help because ontology coercion occurs before the timed
  classification boundary; and
- file-to-report preprocessing needs its own measured/bounded scale gate (or callers must supply
  a preloaded snapshot provider) before this standalone release command is acceptable.

The snapshot/provider API remains the preferred Exact-OM integration path and avoids this file
wrapper load when Exact already owns the view. The licensed SNOMED-scale gate also remains open.

The shared-core hot path was then corrected in pyowl-core `2c5621c`: signature collection now
deduplicates entity `(kind, IRI)` identities across all roots before canonical encoding, and the
signature fingerprint deduplicates encoded bytes rather than structurally hashing entities. A
100,000-root duplicate-heavy comparison retained byte-identical output and reduced this phase
from 7.610 s to 1.124 s (6.77 times faster). Full pyowl-core 3.10/3.12 suites and type/lint gates
passed.

The first post-fix source-checkout run reached the pyELK adapter in 345.27 s, proving that the old
signature boundary was removed, but correctly failed because a bare `PYTHONPATH` checkout has no
installed `pyelk-reasoner` distribution metadata. That invocation is not semantic evidence. The
exact command was repeated with the existing installed pyELK 0.1-series environment, the same
native extension, all three pinned input hashes, and `PYELK_BACKEND=rust`. This valid run completed
file coercion, composition, adapter compatibility, and worker hand-off, then failed with
`ELKTimeoutError` because native classification exceeded its explicit 600.0-second limit. The
outer measurement was 1,486.34 s wall, 3,306.43 s user CPU, and 377.22 s system CPU.

At that point the measured boundary had moved but the real-data gate was not closed:

- no Java path ran and the already-composed view was passed to the pyELK worker;
- no native unsatisfiable set, count, digest, or ROBOT agreement is claimed;
- standalone pre-worker work still lacks its own end-to-end timeout and took the remainder of the
  outer wall window; and
- native pyELK NCIT classification itself now has a reproduced 600-second timeout result and needs
  performance work before the frozen 2,227-class digest can be compared.

Those timeout observations remain useful performance history. The current schema-2 capture
closes the semantic and durable-evidence gaps but confirms that NCIT performance is not yet
acceptable. Exact-OM should still provide its already-loaded view so the file-loading portion is
not repeated.

### Cached-wire diagnostic

A separate non-public diagnostic isolated the shared-data path using the 159,392,315-byte cached
PYOCORE image (SHA-256
`8faa382091db5ca29867ffd9ef58483a497d69ad6d5d0ae501bb72f8d2cf3d96`). With pyowl-core
`354da9a`, eager decode reused the rows already validated during inspection. The worker completed
in 242.119 s: 185.165 s decode, 10.335 s pyELK compilation, 0.178 s native-IR encoding, and
44.297 s native taxonomy. It peaked at about 1.116 GB and returned the same 2,227-class digest.
Compared with the pre-optimization cached-wire profile, total time improved by 30.57% and decode
time by 32.12%. This diagnostic does not replace the official public comparator; it demonstrates
the benefit available when Exact-OM hands off an already-resident or cached shared-core view.

The licensed SNOMED-scale gate remains open because its ontology files are not available on this
runner. No SNOMED correctness or performance claim is inferred from NCIT–DOID.

### Standalone whole-operation watchdog

The public coherence APIs keep the normative reasoner-specific `timeout_s` contract. The
repository-only NCIT comparator alternatively accepts `--overall-timeout` together with
`--timeout none`: it captures and hashes the exact source, target, and alignment bytes once,
parses those retained bytes, composes, reasons, verifies the frozen result, and serializes the
report inside one independently terminable process. The parent receives only a bounded JSON
result. Exceeding the outer deadline raises `ComparatorTimeoutError` and terminates then kills
the worker if necessary; it cannot be misreported as a coherence value.

This implements the previously missing bounded file-to-report mechanism without redefining
`--timeout`, reparsing an OWL path, or weakening the snapshot-first identity API used by Exact.
The tool rejects a numeric inner reasoner timeout in this mode: two nested process owners could
otherwise leave a reasoner worker orphaned when the outer deadline fired. Python 3.10 and 3.12
process tests prove a successful small response, forced termination during pre-reasoner work,
and fail-closed rejection of that unsafe nested configuration. A real-data run must record its
single outer limit explicitly; the mechanism does not turn a timed-out NCIT classification into
performance acceptance.

Schema 2 binds the baseline and three parsed input buffers by SHA-256, records worker and driver
timings, embeds reasoner/core provenance, and fails closed unless the selected backend is
accelerated. For an accelerated result it resolves and hashes the loaded private extension. The
optional `--output` path is create-only, preventing a rerun from silently overwriting prior
evidence.

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

PYELK_BACKEND=rust PYELK_PURE_PYTHON=0 \
  PYTHONPATH=src:../pyOWLCore/src \
  python tools/native_compare.py \
  --source ../Exact-OM/data/bioml_zenodo/ncit-doid/source.owl \
  --target ../Exact-OM/data/bioml_zenodo/ncit-doid/target.owl \
  --alignment ../Exact-OM/data/bioml_zenodo/ncit-doid/refs/train.tsv \
  --reasoner elk --timeout none --overall-timeout 2400 \
  --output reports/O3-ncit-doid-schema2.json
```

Run that command in an environment containing the built native pyELK wheel. Omitting the sibling
pyELK source directory is intentional: it prevents that source tree, which has no compiled
extension in-package, from shadowing the installed native wheel. `PYELK_BACKEND=rust` makes a
missing native extension fail before reasoning, and the comparator independently rejects a
non-accelerated result. The create-only output records the baseline, native backend, worker
count, package version, extension hash, exact input hashes, semantic digest, and timings.
