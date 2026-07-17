# OAEI-Bio-ML-eval specifications

The active plan is [`0.2.0-native-owl-stack.md`](0.2.0-native-owl-stack.md). It is normative for
the `0.2.0` migration but does not describe the current `0.1.0.dev0` runtime as already migrated.

## Release intent

`0.2.0` has three independent promises:

1. existing typed and equivalence metrics retain their results and light dependency surface;
2. coherence consumes shared `pyowl_core.OntologyView` objects and uses Java-free pyHermiT/pyELK;
3. the package and its dependency set support Python 3.10 and newer.

The release is blocked until code, tests, CLI, documentation, packaging, provenance, baselines,
and official scoring policy all satisfy the migration spec. Updating only `requires-python` or
the version is not completion.

## Dependency direction

```text
pyowl-core <--- pyHermiT
    ^             ^
    +--- pyELK    |
          ^       |
          +--- OAEI-Bio-ML-eval

Exact-OM --------> OAEI-Bio-ML-eval  (optional evaluation consumer only)
```

OAEI must not import Exact-OM, pyOWL2Vec*, ROBOT, DeepOnto, JPype, or Java bindings. The base
typed/equivalence installation must not import reasoners.
