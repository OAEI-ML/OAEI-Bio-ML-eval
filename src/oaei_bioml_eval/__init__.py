"""
Common evaluation logic for the OAEI Bio-ML tracks.

Consumed by BOTH the public participant repo (OAEI-Bio-ML) and the private
organiser repo (OAEI-Bio-ML-private), so the same code produces both the
participant's local scores and the official leaderboard numbers.

Planned surface (see OAEI-Bio-ML-private/docs/PROJECT.md §4):

  equivalence/   Track 1 P/R/F1 (global) + MRR/H@k (local), DeepOnto-backed,
                 plus the official reasoner-based coherence (organiser-side)

  typed/         Track 2 Preferred Relation-Aware Typed MRR + H-nDCG@10,
                 dependency-free, seeded from BioKG-Align's scorer
                 
  coherence/     a lightweight STRUCTURAL coherence proxy for participant
                 self-guidance (named distinctly from the official value)
"""

__version__ = "0.1.0.dev0"
