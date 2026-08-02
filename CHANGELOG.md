# Changelog

All notable changes are documented here. This project follows Semantic
Versioning for its public package and CLI contracts.

## 0.2.1 - 2026-08-02

### Changed

- The optional reasoner stack now requires the coordinated `0.2.x` releases of
  `pyowl-core`, `pyHermiT`, and `pyelk-reasoner`.
- Encoded-native handoff validation now negotiates pyOWLCore API `(0, 2)`, model
  schema 2, wire format `(1, 2)`, and `structural-columns` schema 2.
- Release smoke tests and provenance fences reject stale model-schema-1
  reasoners instead of silently accepting or converting their views.

Track 1, Track 2, and coherence metric semantics are unchanged.

## 0.2.0 - 2026-07-17

### Added

- Snapshot-first global, local, and repaired-reference coherence APIs accepting
  existing `pyowl_core.OntologyView` instances.
- Zero-copy source/target composition with a canonical alignment bridge.
- Native pyHermiT and pyELK adapters, verified-wire/mmap bounded ELK execution,
  and deterministic `coherence-provenance/1` reports.
- Bounded public compiler-handoff provenance for reasoner ingestion paths,
  implementation schemas, digests, and available zero-copy/copy counters.
- A fail-closed scalar-versus-encoded performance protocol with isolated warmups/repetitions,
  exact input/result identity, median 25% wall and 20% RSS gates, and explicit counter enforcement.
- A create-only NCIT–DOID schema-2 evidence capture binding the frozen baseline,
  exact inputs, native pyELK binary, semantic digest, and bounded timings.
- Explicit Conference-style micro and Bio-ML-style macro aggregation.
- Python 3.10–3.14 CI, release metadata checks, an SPDX SBOM, and install/API
  documentation.

### Changed

- Minimum Python version is now 3.10.
- Official coherence uses a single Java-free native reasoner stack.
- Alignment TSV terminology is implementation-neutral.
- `reasoner` now installs compatible `pyowl-core`, `pyHermiT`, and
  `pyelk-reasoner` versions; `all` combines the RDF and reasoner extras.
- The NCIT–DOID comparator now calls the current native file API; a regression test prevents the
  removed `backend` option from returning.
- Build and quality-tool configuration excludes non-module Finder/IDE conflict copies from
  release artifacts while leaving local user files untouched.

### Removed

- ROBOT, DeepOnto, JPype, and OWLAPI runtime backends.
- `--backend`, `--robot-jar`, Java command, and heap flags.
- Backend discovery environment variables and the `deeponto` extra.

Removed CLI flags fail with a concise 0.2 migration message. Historical ROBOT
output remains only as a repository development oracle and is not shipped as a
runtime backend.

### Preserved

- `metric-contract/1` freezes the complete Track 1 equivalence and Track 2 typed
  numeric dictionaries and input hashes from 0.1.0.dev0; 0.2.0 does not change
  those metric results.
