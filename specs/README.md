# OAEI-Bio-ML-eval specifications

[`0.2.0-native-owl-stack.md`](0.2.0-native-owl-stack.md) is the normative design
record for the implemented 0.2.0 native shared-OWL migration. Current user-facing
behavior is documented in the root README and `docs/`; work-package evidence is
kept in `reports/`.

## Release intent

`0.2.0` has three independent promises:

1. existing typed and equivalence metrics retain their results and light dependency surface;
2. coherence consumes shared `pyowl_core.OntologyView` objects and uses Java-free pyHermiT/pyELK;
3. the package and its dependency set support Python 3.10 and newer.

The package/version and Python requirement were changed only after the shared
view, native reasoner, Java-deletion, compatibility, documentation, and packaging
work packages landed. External licensed scale runs remain deployment evidence,
not a reason to reintroduce a second runtime path.

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
