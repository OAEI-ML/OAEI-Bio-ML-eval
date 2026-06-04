"""
the two diagnostic families on the hand-traced fixture. Q0 picks the right entity
+ relation; Q1 picks the right entity but the WRONG relation (eq scored highest
on P1, where ssbt was preferred) — so accuracy is 0.5 and the fp/fn paths fire.
"""
from __future__ import annotations

import unittest

from oaei_bioml_eval.typed import diagnostic, loaders
from oaei_bioml_eval.typed.metrics import DEFAULT_RELATIONS

from _fixtures import FIXTURES


class _FixtureScored(unittest.TestCase):
    def setUp(self):
        self.preds = loaders.load_block_format_predictions(
            FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
            relations=DEFAULT_RELATIONS, candidate_count=4)
        self.preferred = loaders.load_preferred_pairs(FIXTURES / "preferred.tsv")
        self.cands = loaders.load_per_query_candidate_sets_from_answers(
            FIXTURES / "answers.tsv")


class TestEntityOnly(_FixtureScored):
    def test_values(self):
        m = diagnostic.score_entity_only_metrics(
            self.preds, self.preferred, self.cands)
        # A: T is 2nd by per-entity max (X 0.95 > T 0.90); B: P1 is 1st
        self.assertAlmostEqual(m["entity_only_mrr"], 0.75)       # mean(.5, 1.0)
        self.assertAlmostEqual(m["entity_only_hits_at_1"], 0.5)  # mean(0, 1)
        self.assertAlmostEqual(m["entity_only_hits_at_5"], 1.0)
        self.assertAlmostEqual(m["entity_only_hits_at_10"], 1.0)


class TestRelationOnPreferredEntity(_FixtureScored):
    def test_values(self):
        m = diagnostic.score_relation_on_preferred_entity_metrics(
            self.preds, self.preferred, self.cands)
        # A: eq highest on T == preferred eq (correct);
        # B: eq highest on P1 != preferred ssbt (wrong)
        self.assertAlmostEqual(m["relation_accuracy_on_preferred_entity"], 0.5)
        # eq: tp1 fp1 fn0 -> f1 = 2/3 ; ssbt: tp0 fp0 fn1 -> f1 = 0
        self.assertAlmostEqual(
            m["relation_f1_on_preferred_entity_equivalent"], 2 / 3)
        self.assertAlmostEqual(
            m["relation_f1_on_preferred_entity_source_subsumed_by_target"], 0.0)
        self.assertAlmostEqual(
            m["relation_macro_f1_on_preferred_entity"], (2 / 3 + 0.0) / 2)

    def test_skips_query_with_no_rows_on_preferred_entity(self):
        # preferred entity outside the candidate filter -> undefined, skipped
        preferred = {("SA", "Q0"): [("GHOST", "equivalent")]}
        m = diagnostic.score_relation_on_preferred_entity_metrics(
            self.preds, preferred, self.cands)
        self.assertEqual(m["relation_accuracy_on_preferred_entity"], 0.0)


if __name__ == "__main__":
    unittest.main()
