# OAEI-Bio-ML-eval

Houses the common evaluation logic for both the public and private facing repos in OAEI-Bio-ML.

Both `OAEI-Bio-ML` (participant-facing) and `OAEI-Bio-ML-private` (organiser-side) depend on this package, so participants self-score with the *same* code that produces the official leaderboard numbers _(the coherence-based metrics are still TBD)_.

Note that this repo is BioML-owned (signaled by the explicit naming convention). This is deliberate. Although logic may be shared across multiple OAEI-ML tracks (e.g., Bio-ML and BioKG-Align); the different tracks can evolve in different directions over time. As such, this repo should only ever be required as a dependency from `OAIE-Bio-ML` and `OAEI-Bio-ML-private`.

## Status

`v0.1.0.dev0` — package skeleton only. 

The evaluation modules are implemented in the scoring phase (see `docs/PRD.md` in `OAEI-Bio-ML-private`).

## Planned layout

- `equivalence/` — Track 1 metrics: Precision/Recall/F1 (global) and MRR/Hits@k (local), plus the **official reasoner-based coherence** (organiser-side).
- `typed/` — Track 2 metrics: Preferred Relation-Aware (Typed) MRR and Hierarchy-Aware Typed nDCG@10. Dependency-free; seeded from BioKG-Align.
- `coherence/` — a lightweight **structural coherence proxy** for participant self-guidance, named distinctly from the official reasoner value.

## Packaging

PEP 621 + hatchling, so it is consumable by uv, Poetry, and plain pip alike.
