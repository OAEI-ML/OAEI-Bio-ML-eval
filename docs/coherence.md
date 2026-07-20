# Coherence API and semantics

## Meaning of the metric

`global_coherence` is the number of unsatisfiable named classes divided by the
named-class signature of the composed source/target ontology. It is historically
named but semantically a degree of incoherence: zero is clean. `owl:Thing` and
`owl:Nothing` are excluded symmetrically from numerator and denominator.

`local_coherence` is the mean blame flag over each query's committed rank-1
mapping. Empty candidate lists commit no mapping and do not contribute a query.
The 0.2 migration preserves this established batched-blame behavior.

## In-memory API

```python
from oaei_bioml_eval.coherence import (
    score_global_coherence,
    score_local_coherence,
    score_reference_coherence,
)

global_report = score_global_coherence(pairs, source_view, target_view)
local_report = score_local_coherence(committed_top1, source_view, target_view)
reference_report = score_reference_coherence(
    relation_typed_pairs, source_view, target_view, reasoner="elk"
)
```

The exact source and target views are retained in a composite. Correspondences
are normalized, deduplicated, self-pairs are dropped, and only an O(M) bridge
delta is added. Source and target arenas remain unchanged. Existing views from
Exact-OM or another `pyowl-core` consumer therefore reach the reasoners without
OWL serialization or reparsing.

## File and provider API

`score_*_coherence_files` accepts the complete `pyowl_core.OntologyInput`
surface: paths, bytes, caller-owned streams, providers exposing `owl_snapshot`,
and existing views. Each input is coerced once. Caller-owned streams stay open.
Document IRIs, load options, import resolvers, and cancellation tokens are
forwarded unchanged.

Malformed correspondence IRIs fail before ontology loading. Set
`skip_invalid=True` or pass `--skip-invalid-iris` to drop them with a warning and
record the dropped count in provenance.

## Reasoner policy

- `hermit` is the exact OWL 2 DL default.
- `elk` is the OWL 2 EL path and is labeled a lower bound.
- Only the public cooperative pyHermiT timeout triggers ELK fallback.
- The same composite object is used for a fallback; ordinary errors, worker
  failures, profile failures, and ELK timeouts propagate.
- An inconsistent composite uses classical explosion: the full denominator is
  unsatisfiable and the degree is 1.0.

Bounded ELK execution sends verified `pyowl-core` wire bytes to a spawned worker.
The worker must report zero OWL parses and matching structural, logical, and
signature fingerprints. It never receives an ontology path or pickled graph.

## Provenance

Every official report includes `coherence-provenance/1`, covering core API/model/
wire versions, role manifests and fingerprints, import diagnostics, bridge and
result digests, requested/actual reasoner, package/backend versions, profile
completeness, transport, elapsed time, timeout, and fallback attempts. Semantic
digests exclude paths, credentials, timestamps, and object IDs.

When the public reasoner facade supplies compiler diagnostics, `compiler_handoff`
also records its selected ingestion path, canonical compiler digest, compiler-cache
schema, private-IR schema, native ABI version, and the bounded copy/ownership counters
that are actually available. Missing diagnostics stay absent; the evaluator neither
infers acceleration from a package version nor fabricates zero counters.

## Encoded performance protocol

The repository benchmark keeps its existing schema-2 single-run diagnostic mode. Supplying a
scalar `--baseline-backend` enables the additive acceptance comparator:

```text
PYTHONPATH=src:../pyOWLCore/src:../pyELK/src \
  python3 benchmarks/bench_o3_native.py \
  --reasoner elk --baseline-backend python --backend rust \
  --warmups 1 --repetitions 5 --enforce
```

Each sample runs in a fresh spawned process so process peak RSS is independent of earlier samples.
Baseline and candidate order alternates. The report requires identical input and semantic hashes,
uses median retained pipeline wall time and peak RSS, and evaluates candidate/scalar ratios against
the maximum `1.25` wall and `1.20` RSS gates. Enforcement additionally requires every baseline to
report a scalar ingestion path and every candidate to report `encoded-native`, complete public
counter coverage, no benchmark wire encode, and zero forbidden parser, resolver, scalar
materialization, structural-copy, wire, base-flattening, and per-row-FFI counters.

The built-in generated workload is a protocol exercise only. Its report always says
`diagnostic_only: true`, `release_gate_eligible: false`, and `release_accepted: false`; a zero exit
from `--enforce` would validate those mechanics, not replace pinned Conference/Bio-ML, GO/NCIT, or
licensed SNOMED-scale evidence. With the current unadvertised sibling capabilities, enforcement is
expected to fail closed on the selected path and missing counters.
