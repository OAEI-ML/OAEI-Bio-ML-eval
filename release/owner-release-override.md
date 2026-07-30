# Release-owner authorization for 0.2.0

Date: 2026-07-30

The release owner authorizes production publication of
`oaei-bioml-eval` 0.2.0 and closes the remaining external release gates for
this initial publication. This authorization covers environment-protected PyPI
trusted publication in dependency order after the final compatible 0.1-series
sibling artifacts have passed their package checks.

The decision explicitly accepts the retained limitations recorded in
`reports/O5-release.md`: the available DOID-scale performance evidence,
unavailable licensed SNOMED-scale inputs, the absence of a new official
leaderboard dry run, and the use of portable wheels while additional native
platform wheels are built. Closing these gates is an accountable owner waiver;
it does not rewrite the historical measurements or claim that an unexecuted
test passed.

PyPI upload verification, artifact hashes, and clean-environment installation
remain mandatory operational checks and are not waived. The tag-scoped release
workflow must build the wheel and source distribution twice, prove byte
reproducibility, audit and hash the exact pair, smoke the wheel without optional
dependencies, and attest both distributions before its single OIDC publication
job. No credential or token is stored in the repository.
