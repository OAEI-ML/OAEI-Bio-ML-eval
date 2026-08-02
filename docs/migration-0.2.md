# Migrating to 0.2

## Runtime change

Official coherence now requires the `reasoner` extra and consumes shared
`pyowl-core` views. There is no Java runtime, external merge command, or warm-JVM
backend.

```bash
python -m pip install --upgrade 'oaei-bioml-eval[reasoner]==0.2.1'
```

Replace backend selection with `--reasoner hermit` or `--reasoner elk`:

| Removed 0.1 surface | 0.2 replacement |
|---|---|
| `--backend robot` | `--reasoner hermit` |
| `--backend deeponto` | `--reasoner hermit` or `elk` |
| `--robot-jar`, Java command, heap flags | remove; no replacement needed |
| backend discovery environment variable | remove; native dispatcher is fixed |
| `[deeponto]` extra | `[reasoner]` |

Passing a removed process/backend flag produces `was removed in 0.2.0` instead
of attempting a compatibility shim.

Version 0.2.1 moves the complete optional reasoner stack to the pyOWLCore 0.2
contract. Environments must upgrade `pyowl-core`, `pyHermiT`, and
`pyelk-reasoner` together; model-schema-1 views and encoded attestations are
rejected rather than converted.

## Reusing an Exact-OM load

Pass Exact-OM's existing `OntologyView` objects to `score_global_coherence`,
`score_local_coherence`, or `score_reference_coherence`. Do not serialize them to
temporary OWL files. OAEI-Bio-ML-eval depends only on the shared core protocol;
it does not import Exact-OM.

Code that only has paths may continue using the `*_files` functions. Their
source and target parameters now accept the broader `OntologyInput` protocol,
and each is loaded exactly once.

## Metric compatibility

Track 1 equivalence and Track 2 typed key names, numeric types, ordering,
quantization, TSV formats, and empty-input behavior are unchanged. The committed
`metric-contract/1` baseline identifies its original 0.1.0.dev0 capture and must
remain byte-identical.

Coherence reports add deterministic provenance. Existing historical metric keys
remain, but consumers should preserve the provenance object and respect
`lower_bound`, `inconsistent`, and `fallback_reason` semantics.
