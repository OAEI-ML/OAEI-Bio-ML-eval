# OAEI-Bio-ML-eval documentation

OAEI-Bio-ML-eval provides reproducible scoring for the OAEI Bio-ML tracks.
Install only the surface you need:

```bash
python -m pip install oaei-bioml-eval
python -m pip install 'oaei-bioml-eval[rdf]'
python -m pip install 'oaei-bioml-eval[reasoner]'
python -m pip install 'oaei-bioml-eval[all]'
```

The base package scores TSV submissions without third-party runtime
dependencies. The `rdf` extra adds OAEI Alignment RDF input, while `reasoner`
adds Java-free pyHermiT and pyELK coherence evaluation.

## Choose a guide

- [Installation and deployment](installation.md) covers supported Python
  versions, optional dependencies, offline wheelhouses, and licenses.
- [Coherence API and semantics](coherence.md) explains global/local coherence,
  reasoner selection, shared ontology views, timeout behavior, and provenance.
- [Migrating to 0.2](migration-0.2.md) describes the Java-free runtime and
  compatibility changes.
- The repository [README](../README.md) contains the shortest Python and CLI
  examples.

## Command-line entry points

| Command | Purpose |
|---|---|
| `oaei-bioml-equivalence-score` | Track 1 global and local equivalence scoring |
| `oaei-bioml-typed-score` | Track 2 relation-aware ranking scoring |
| `oaei-bioml-coherence-score` | Organiser-facing ontology coherence scoring |

Run any command with `--help` for its complete argument contract. Use a locked
environment for leaderboard production and retain the emitted provenance beside
every result.
