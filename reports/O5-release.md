# O5 Python 3.10 compatibility and native release evidence

## Outcome

The bounded O5 implementation prepares `oaei-bioml-eval` 0.2.0 as a typed,
Java-free Python package supporting Python 3.10 and newer. The base wheel has no
runtime dependencies. RDF parsing and native coherence reasoning are explicit
extras, and the evaluator consumes the shared `pyowl-core` ontology view rather
than reparsing ontology inputs for each reasoner.

This is release-preparation evidence, not a claim that the complete ecosystem is
ready for public deployment. Publication remains gated on final 0.1-series
`pyowl-core`, pyHermiT, and pyELK distributions, acceptable native reasoner
performance on large ontologies, and the external-data acceptance runs described
below.

## Release contract

- Project and package versions are coordinated at `0.2.0`.
- `requires-python` is `>=3.10`, and CI declares CPython 3.10 through 3.14.
- The base install is dependency-free; `rdf`, `reasoner`, and `all` extras make
  optional capabilities explicit.
- The wheel is marked typed with `py.typed`.
- Hatch builds both wheel and source distribution; Twine validates both.
- The source distribution includes the changelog, documentation, specifications,
  evidence reports, licence, and SPDX 2.3 SBOM.
- Installation, native coherence, Exact-OM/shared-view reuse, provenance, and the
  0.2 migration are documented.
- Ruff, strict mypy, compileall, the metric contract, and the full unit suite are
  release gates.

The native dependency order is intentional: publish `pyowl-core` 0.1.x first,
then compatible pyHermiT and pyELK 0.1.x wheels, and only then publish
`oaei-bioml-eval` 0.2.x. Development prereleases do not satisfy the final
`>=0.1,<0.2` constraints and must not be made resolver-compatible by weakening
the release metadata.

## Installed-wheel evidence

A compiler-free wheelhouse was assembled from temporary copies of the sibling
source trees with their prospective final versions set to 0.1.0. No sibling
repository was modified by this exercise. Offline pip resolution of
`oaei-bioml-eval[reasoner]==0.2.0` succeeded on Python 3.10 and 3.12, and
`pip check` reported no conflicts.

The installed-wheel smoke test used a four-class equivalence clash and exercised
both public reasoner facades. pyHermiT and bounded pyELK each returned the exact
two unsatisfiable classes and global coherence `0.5`; the bounded worker retained
the verified shared-view transport. Independent `--no-deps` base-wheel installs
also passed the equivalence and typed CLI golden cases and exposed coherence help
without importing optional reasoner packages.

These temporary prospective wheels establish resolver and install behaviour only.
They are not published artefacts and do not replace the sibling projects' own
release gates.

## Verification matrix

The locally executed matrix covers CPython 3.10.11 and 3.12.3. Python 3.11,
3.13, and 3.14 are declared in CI but were not locally available and are not
reported as locally tested.

| Gate | CPython 3.10 | CPython 3.12 |
|---|---:|---:|
| installed-native suite (2026-07-18 refresh) | 178 passed, 6 optional skips, 24 subtests | 178 passed, 6 optional skips, 24 subtests |
| current source/comparator suite | 180 passed, 6 optional skips | 180 passed, 6 optional skips |
| full suite with RDFLib 7.6.0 | 177 passed | not locally run |
| `metric-contract/1` | pass | pass |
| compileall (`src`, `tools`, `tests`, `benchmarks`) | pass | pass |
| base-wheel install and CLI smoke | pass | pass |
| offline `[reasoner]` resolution and `pip check` | pass | pass |
| installed HermiT/ELK exact clash smoke | pass | pass |

Ruff passes the complete maintained source/tool/test/benchmark surface, and
strict mypy passes all 28 checked source/tool files. The final
`oaei_bioml_eval-0.2.0-py3-none-any.whl` and
`oaei_bioml_eval-0.2.0.tar.gz` build successfully, Twine validates both, and the
source archive contains the release documentation, specifications, reports,
SBOM, licence, and typed package sources.

## Performance measurements and release blocker

These measurements use the prospective final pure-Python sibling wheel stack on
the local Python 3.12 runner. They are diagnostic measurements, not scale
acceptance evidence.

| Workload | Exact semantic result | Wall time | Peak process RSS |
|---|---:|---:|---:|
| shared-view bridge, 5,000 pairs / 10,000 denominator | identities retained | normalization 0.013 s; composition 2.169 s; signature 1.628 s | 46.2 MB |
| pyELK, 400 classes per side / 100 bridges | 200/800 unsatisfiable | reasoning 6.044 s | 55.0 MB |
| pyHermiT, 10 classes per side / 2 bridges | 4/20 unsatisfiable | reasoning 32.106 s | 43.2 MB |

The pyELK workload was approximately 3.3 times slower than the earlier O3
Python-3.12 measurement (6.044 s versus 1.848 s). The small pyHermiT workload was
approximately 7.6 times slower (32.106 s versus 4.22 s). Exact outputs still
matched, but these regressions fail the intended performance gate. O5 therefore
does **not** accept large-ontology performance. Native work in pyHermiT/pyELK
(WPR2-WPR4) must close this blocker before ecosystem release.

For completeness, the bridge run recorded a 3.336 s cold provenance build and a
0.018 s warm build. The pyELK run encoded 385,780 wire bytes and spent 1.203 s in
compile plus 4.788 s in classification. The pyHermiT run encoded 12,860 wire
bytes and spent 0.169 s in compile plus 31.697 s in classification. These stage
figures make the present bottlenecks visible; they do not establish a speedup.

## External acceptance and deployment status

The pinned NCIT-DOID source, target, and alignment match the frozen ROBOT evidence. The current
schema-2 capture is persisted as `reports/O3-ncit-doid-schema2.json`, whose SHA-256 is
`23ee9f77cc8fb24e4b2652173f5e4b87e25b36aa4d867810113d283459e917b7`. It binds the exact input
buffers, baseline, pyowl-core provenance, current pyELK `1a05d16` Rust backend, and loaded native
binary. It returned 24,227 named classes, 1,406 correspondences, and exactly 2,227 unsatisfiable
classes with digest `8dd56db2f864e757fb9fe04ca9b4cb6798e161597ff715f81175129db8bc27ab`,
equal to the frozen Java ELK result.

The run closes the NCIT semantic and durable-evidence gates, but not performance acceptance:
pyELK classification took 881.863 s and the bounded parse/compose/reason/report worker took
1,162.032 s, versus 22.47 s for the recorded Java oracle. Standalone RDF/XML loading also selected
pyowl-core's complete Python fallback because the installed core wheel did not advertise native
RDF/XML parsing. See the O3 report for the exact command, hashes, caveats, historical attempts,
and cached-view profile. Licensed SNOMED-scale inputs remain unavailable. The official leaderboard
was not rerun end to end; only installed deployment, scoring smokes, and the pinned NCIT-DOID
acceptance run were performed.

Accordingly, the bounded O5 implementation is complete enough to commit and hand
off, while publication/deployment remains blocked by:

1. final compatible 0.1-series sibling releases;
2. further native performance optimization and repeatable large-ontology benchmarks;
3. NCIT-scale performance acceptance and the licensed SNOMED-scale gate; and
4. an end-to-end official leaderboard dry run on the resolved release artefacts.

## Reproduction

```text
PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYHERMIT_BACKEND=python PYELK_PURE_PYTHON=1 \
  python3.10 -m unittest discover -s tests -v
PYTHONPATH=src:../pyOWLCore/src:../pyHermiT/src:../pyELK/src \
  PYHERMIT_BACKEND=python PYELK_PURE_PYTHON=1 \
  python3.12 -m unittest discover -s tests -v

PYTHONPATH=src python3.10 tools/metric_contract.py
PYTHONPATH=src python3.12 tools/metric_contract.py
python -m compileall -q src tools tests benchmarks
ruff check src tools tests benchmarks
mypy --strict src

python -m build --no-isolation
twine check dist/*
python -m pip install --no-index --find-links <wheelhouse> \
  'oaei-bioml-eval[reasoner]==0.2.0'
python tools/installed_native_smoke.py
```
