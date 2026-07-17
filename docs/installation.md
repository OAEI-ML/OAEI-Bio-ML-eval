# Installation and deployment

## Supported Python

OAEI-Bio-ML-eval 0.2.0 requires Python 3.10 or newer. CI covers CPython 3.10,
3.11, 3.12, 3.13, and 3.14. The installable evaluator itself contains no native
extension and does not require a compiler.

## Installation profiles

| Command | Surface | Added dependencies |
|---|---|---|
| `pip install oaei-bioml-eval` | TSV metrics and structural proxy | none |
| `pip install 'oaei-bioml-eval[rdf]'` | OAEI Alignment RDF | RDFLib 7.x |
| `pip install 'oaei-bioml-eval[reasoner]'` | official coherence | pyowl-core, pyHermiT, pyELK 0.1.x |
| `pip install 'oaei-bioml-eval[all]'` | all supported surfaces | both groups |

Use a locked deployment environment for leaderboard jobs. Store the installed
distribution list and emitted coherence provenance next to every result. Do not
combine numbers with different metric-methodology or reasoner versions without
an explicit methodology review.

Publish in dependency order: `pyowl-core` 0.1.x, then pyHermiT and pyELK 0.1.x,
then OAEI-Bio-ML-eval 0.2.x. Pip correctly rejects development prereleases when
the reasoner metadata requests a final 0.1 core; do not bypass that check in an
official wheelhouse.

## Compiler-free and accelerated execution

The evaluator is pure Python. Its reasoner dependencies provide compiler-free
implementations and may also supply platform wheels with Rust acceleration. A
machine without a compiler must install a supported wheel or the dependency's
pure distribution; it never falls back to Java. Backend name, acceleration
status, implementation version, worker count, and fallback reason are recorded
in result provenance.

If a requested reasoner package or its frozen public API is unavailable, official
coherence fails with an actionable `NativeReasonerUnavailableError` or
`NativeReasonerCompatibilityError`; it does not silently substitute another
runtime.

## Offline deployment

Build a wheelhouse on a connected machine for the exact target platform:

```bash
python -m pip download --only-binary=:all: \
  'oaei-bioml-eval[all]==0.2.0' -d wheelhouse
python -m pip install --no-index --find-links wheelhouse \
  'oaei-bioml-eval[all]==0.2.0'
```

Smoke-test all three commands and one native fixture before promoting the
wheelhouse. The package-level wheel is `py3-none-any`; accelerated dependency
wheels are platform-specific.

## Dependency and license boundary

- OAEI-Bio-ML-eval: Apache-2.0.
- pyowl-core: Apache-2.0.
- pyELK: Apache-2.0.
- pyHermiT: LGPL-3.0-or-later.
- RDFLib: BSD-3-Clause.

The reasoners and RDFLib are optional, separately distributed works. Review the
licenses of the exact resolved artifacts used by a deployment. The committed
[SPDX SBOM](../SBOM.spdx.json) records the declared release ranges; deployment
jobs should additionally archive a resolver-generated environment SBOM.
