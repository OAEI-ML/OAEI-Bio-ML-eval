# OAEI-Bio-ML-eval

[![PyPI](https://img.shields.io/pypi/v/oaei-bioml-eval)](https://pypi.org/project/oaei-bioml-eval/)
[![Python](https://img.shields.io/pypi/pyversions/oaei-bioml-eval)](https://pypi.org/project/oaei-bioml-eval/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

OAEI-Bio-ML-eval is the shared, versioned scoring package for the public and
organiser-facing OAEI Bio-ML repositories. Version 0.2.0 supports Python 3.10+
and removes Java from the evaluation runtime.

The package provides:

- Track 1 equivalence Precision/Recall/F1 and local MRR/Hits@k;
- Track 2 preferred relation-aware MRR and hierarchy-aware typed nDCG@10;
- explicit micro and macro cross-task aggregation; and
- organiser-computed alignment coherence over shared OWL views using native
  pyHermiT or pyELK.

Typed/equivalence metric cores and alignment-TSV loaders have no runtime
dependencies. RDF parsing and native reasoning are opt-in extras.

## Installation

```bash
python -m pip install oaei-bioml-eval
python -m pip install 'oaei-bioml-eval[rdf]'
python -m pip install 'oaei-bioml-eval[reasoner]'
# both optional surfaces
python -m pip install 'oaei-bioml-eval[all]'
```

The reasoner extra installs compatible 0.1-series releases of `pyowl-core`,
`pyHermiT`, and `pyelk-reasoner`. Supported wheels require neither Java nor a
local Rust/C compiler; dependency-level accelerated implementations may be used
when available, while their compiler-free implementations preserve portability.
See [installation and deployment](docs/installation.md).

## Python quick start

The metric core accepts ordinary Python collections and has no runtime
dependencies:

```python
from oaei_bioml_eval.equivalence import global_prf1

predicted = {("mouse:Heart", "human:Heart"), ("mouse:Liver", "human:Liver")}
reference = {("mouse:Heart", "human:Heart")}

scores = global_prf1(predicted, reference)
assert scores["precision"] == 0.5
assert scores["recall"] == 1.0
```

## CLI

```bash
oaei-bioml-equivalence-score global submission.tsv reference.tsv
oaei-bioml-typed-score submission.block.tsv answers.tsv

oaei-bioml-coherence-score global submission.tsv source.owl target.owl \
  --reasoner hermit --timeout 7200 --output coherence.json
oaei-bioml-coherence-score local local.test.ranked.tsv source.owl target.owl \
  --reasoner elk
```

Coherence is a degree of incoherence: `0` is clean and larger values are worse.
HermiT is the exact default. A cooperative HermiT timeout alone falls back to
ELK over the same composite and marks the result as an EL lower bound. The
reserved `structural` command is not currently available: its rule set remains
open and invoking it raises `NotImplementedError`.

## Shared-view API

Callers that already loaded ontologies—such as Exact-OM—can pass the same
`pyowl_core.OntologyView` objects directly, avoiding serialization and duplicate
parsing:

```python
from oaei_bioml_eval.coherence import score_global_coherence

report = score_global_coherence(
    [("https://example.org/Source", "https://example.org/Target")],
    source_view,
    target_view,
    reasoner="hermit",
    timeout_s=7200.0,
)
```

Path, byte, stream, provider, and existing-view inputs are also accepted by the
`*_files` wrappers. Each source and target input is coerced once through
`pyowl-core`; one zero-copy composite is shared by signature calculation and
reasoning. OAEI-Bio-ML-eval never imports Exact-OM, so dependency direction
remains clean. See [coherence API and semantics](docs/coherence.md).

## Aggregation policy

Use `aggregate_across_tasks(..., average="micro")` for Conference-style pooled
evaluation and `average="macro"` for equal task weighting used by Bio-ML.
Micro aggregation recomputes metrics from sufficient statistics; it never
approximates them by averaging already-computed percentages.

## Reproducibility

Official coherence output includes `coherence-provenance/1`: package and API
versions, semantic fingerprints, normalized bridge digest, requested/actual
reasoner, timeout/fallback details, profile status, and numerator/denominator
digests. Local paths, timestamps, and Python object identities are excluded from
semantic digests.

The pre-migration metric contract remains byte-frozen in
`tests/baselines/metric-contract-v1.json`. Historical ROBOT 1.9.10 JSON is kept
only as a quarantined development oracle; it is outside the installable package
and ordinary CI.

## Release information

- [Documentation index](docs/index.md)
- [0.2 migration guide](docs/migration-0.2.md)
- [changelog](CHANGELOG.md)
- [SPDX SBOM](SBOM.spdx.json)
- [native-stack specification](specs/0.2.0-native-owl-stack.md)
- [release evidence](reports/O5-release.md)

Licensed under Apache-2.0. Optional dependencies retain their own licenses; see
[dependency and license notes](docs/installation.md#dependency-and-license-boundary).
