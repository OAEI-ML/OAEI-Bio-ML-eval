# Encoded reasoner compatibility checkpoint

Date: 2026-07-20. OAEI-Bio-ML-eval revision: `fcebff1`. Coordinated source candidates:
pyOWLCore `34b9e84`, pyELK `886f6a3`, and pyHermiT `3c56fc2`.

## Outcome

The repository-owned 0.2.x handoff layer is implemented as a fail-closed checkpoint. OAEI builds
one source/target/bridge `OntologyComposite`, gives that exact identity to the selected public
reasoner, and records bounded compiler evidence without inspecting encoded columns or reasoner
internals. Timeout workers persist one verified core artifact and reopen it through public mmap
APIs rather than reparsing OWL sources.

This checkpoint does **not** complete the 0.2.x acceptance package. The coordinated encoded
capabilities remain unadvertised, and installed Python/platform matrices, accepted biomedical and
licensed-scale performance, final dependency ranges, artifact audits, and the public compatibility
matrix remain open. Global/reference/local coherence semantics and result keys are unchanged.

## Implemented repository-owned slice

- In-process adapters preserve source, target, composite, adapter, and reasoner ownership identity.
  OAEI passes only the public view; it never flattens the composite, exports private IR, or hands
  source paths to a reasoner.
- The bounded ELK worker writes one core wire artifact, reopens it with `mmap=True, verify=True`,
  requires `wire-verified` plus `mmap-snapshot`, reports zero OWL parses, and retains the mapped
  owner through reasoner shutdown. Temporary paths never enter provenance.
- `coherence-provenance/1` contains an additive `compiler_handoff` block. It validates the frozen
  public core descriptor, owner/storage kind, requested and selected reasoner/backend, compiler and
  IR schemas, implementation/ABI version, canonical compiler digest, phase timings, and an
  allowlisted materialization/copy/parser/resolver/wire/FFI counter ledger.
- Unknown private fields are discarded at the initial public facade boundary and rejected if they
  cross the frozen provenance boundary. Malformed known fields fail closed. Scalar sessions cannot
  claim encoded-only timing, nonzero encoded buffers/copies/private IR, or released-GIL evidence.
- `benchmarks/bench_o3_native.py` schema 2 removes the unconditional in-process core-wire encode,
  enforces composite/reasoner identity, records wall/CPU/RSS by phase, uses public compiler timing
  and counters, reports missing counter coverage honestly, and binds input, denominator,
  unsatisfiable-result, structural, and canonical-provenance hashes.
- Base and RDF-only installations remain independent of the optional shared OWL/reasoner stack;
  the reasoner extra remains Java-free and metric behavior is independent of acceleration.

The implementation sequence is represented by `6dec23f`, `ac751b5`, `0de124c`, `992d918`,
`ecb4235`, `7c65fa0`, `d0b7c87`, `2dd1fc2`, and `fcebff1`.

## Local verification at this checkpoint

Against pyELK `886f6a3`, pyHermiT `3c56fc2`, and the current core source candidate, the complete
OAEI suite passed 202 tests plus 69 subtests. Ruff passed over `src`, `tests`, `tools`, and
`benchmarks`; strict mypy passed 27 source files with sibling source typing enabled. Focused
benchmark smoke tests also prove that an in-process reasoner receives the exact composite and that
the benchmark itself performs zero core-wire encodes.

These local results validate the handoff implementation, not the release-scale timing and memory
thresholds.

## Acceptance ledger

| 0.2.x requirement | Checkpoint state |
|---|---|
| One composite identity handed to an in-process public reasoner | Implemented and tested |
| Verified wire/mmap worker with zero source reparses | Implemented and tested |
| Bounded public compiler provenance and schema validation | Implemented and tested |
| Missing counters remain unavailable rather than fabricated | Implemented and tested |
| No unconditional in-process wire encode in the benchmark | Implemented and tested |
| Metric/result behavior unchanged across the existing suite | 202 tests + 69 subtests pass |
| Forced encoded pyELK/pyHermiT paths | Open until reasoner capabilities are advertised |
| Pure/scalar/native Python-version and installed-wheel matrix | Open |
| Conference/Bio-ML, GO/NCIT, and licensed SNOMED-scale evidence | Open |
| Maximum 25% wall and 20% RSS regression gates | Open; no performance claim is made |
| Final compatible releases/ranges and audited artifacts | Open |
| Official deployment and leaderboard dry run | Open under the 0.2.0 release plan |

## Release decision

Keep the encoded compatibility path unadvertised and do not widen dependency ranges from source
revisions alone. Completion requires exact metric/result parity in the installed pure/scalar/native
matrix, complete zero-materialization/copy evidence, accepted scale records, audited distributions,
and a published table naming the exact core, pyELK, pyHermiT, and OAEI revisions. Until then this
checkpoint is compatibility groundwork, not a speedup or release claim.
