"""
Common evaluation logic for the OAEI Bio-ML tracks.

Consumed by BOTH the public participant repo (OAEI-Bio-ML) and the private
organiser repo (OAEI-Bio-ML-private), so the same code produces both the
participant's local scores and the official leaderboard numbers.

Planned surface (see OAEI-Bio-ML-private/docs/PROJECT.md §4):

  equivalence/   Track 1 P/R/F1 (global) + MRR/H@k (local)

  typed/         Track 2 Preferred Relation-Aware Typed MRR + H-nDCG@10,
                 dependency-free, seeded from BioKG-Align's scorer

  coherence/     official native reasoner-based coherence (organiser-side);
                 its reserved structural-proxy API is unavailable while the
                 rule set remains open
"""

__version__ = "0.2.1"
