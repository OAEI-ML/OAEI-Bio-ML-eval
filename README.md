# OAEI-Bio-ML-eval

This package houses common evaluation logic for the public and organiser-facing OAEI Bio-ML
repositories. Participants and organisers should calculate leaderboard metrics with the same
versioned code.

## Current status

The repository metadata is currently `0.1.0.dev0` and requires Python 3.12. Typed Track 2 and
equivalence Track 1 metrics are implemented and tested. Coherence is also implemented, but its
official reasoner path currently depends on Java through an external ROBOT process or optional
DeepOnto/JPype.

The planned `0.2.0` migration removes those Java paths, supports Python 3.10 and newer, and
classifies shared `pyowl_core` ontology views with native pyHermiT and pyELK. This is a plan, not
a claim about the current runtime. See the [specification index](specs/README.md) and the
[`0.2.0` migration specification](specs/0.2.0-native-owl-stack.md).

The migration deliberately separates two concerns:

- typed/equivalence scoring and their file loaders remain lightweight and behavior-frozen; and
- official coherence gains snapshot-first APIs and Java-free optional reasoners.

## Modules

- `equivalence/` — Track 1 global Precision/Recall/F1 and local MRR/Hits@k.
- `typed/` — Track 2 Preferred Relation-Aware MRR and Hierarchy-Aware Typed nDCG@10.
- `coherence/` — official reasoner-based degree of incoherence plus a distinctly named
  structural participant proxy.

OAEI-Bio-ML-eval remains BioML-owned so tracks can evolve independently. It must not depend on
Exact-OM; both may share the lower-level OWL core and reasoner packages.

## Cross-task averaging

The metric families expose an explicit `aggregate_across_tasks(..., average=...)` choice.
Use `average="micro"` for Conference-style pooled evaluation and `average="macro"` for the
equal-task weighting used by Bio-ML and other multi-dataset reports. Micro aggregation recomputes
metrics from counts/query denominators; it does not average already-computed percentages.

## Packaging

The package uses PEP 621 and hatchling and is consumable with uv, Poetry, and pip. The base
installation stays small. RDF alignment parsing and native coherence remain explicit extras in
the `0.2.0` plan.
