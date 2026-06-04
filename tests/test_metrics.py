"""
the typed metrics. the headline cases run on the on-disk fixture (a hand-traced
2-query scenario); the expected numbers come from that trace. ranking order,
quantization, and the H-nDCG arithmetic also get focused unit tests.
"""
from __future__ import annotations

import unittest

from oaei_bioml_eval.typed import loaders, metrics
from oaei_bioml_eval.typed.metrics import DEFAULT_RELATIONS

from _fixtures import FIXTURES, chain_hierarchy

# H-nDCG values for the fixture, derived from the hand-traced rankings + gains.
NDCG_A = 0.7340895391998685     # Q0 (equivalence) query
NDCG_B = 0.6696718164942300     # Q1 (subsumption) query


def _row(tgt, rel, score):
    return {"SrcEntity": "S", "TgtEntity": tgt, "Relation": rel,
            "Score": str(score)}


class TestPrimitives(unittest.TestCase):
    def test_relation_sort_key(self):
        self.assertEqual(metrics._relation_sort_key("equivalent"), 0)
        self.assertEqual(metrics._relation_sort_key("source_subsumed_by_target"), 1)
        self.assertEqual(metrics._relation_sort_key("source_subsumes_target"), 2)
        self.assertEqual(metrics._relation_sort_key("???"), 3)  # unknown sorts last

    def test_mean(self):
        self.assertEqual(metrics.mean([1.0, 2.0, 3.0]), 2.0)
        self.assertEqual(metrics.mean([]), 0.0)

    def test_default_relations_from_frozen_order(self):
        self.assertEqual(metrics.DEFAULT_RELATIONS, (
            "equivalent", "source_subsumed_by_target", "source_subsumes_target"))


class TestRanking(unittest.TestCase):
    def test_tie_break_relation_then_target(self):
        # all the same score -> order by relation (eq<ssbt<sst) then target
        rows = [
            _row("B", "source_subsumes_target", 0.5),
            _row("A", "source_subsumed_by_target", 0.5),
            _row("B", "equivalent", 0.5),
            _row("A", "equivalent", 0.5),
        ]
        ranked = metrics._rank_predictions_for_source(rows)
        self.assertEqual(
            [(r["TgtEntity"], r["Relation"]) for r in ranked],
            [("A", "equivalent"), ("B", "equivalent"),
             ("A", "source_subsumed_by_target"),
             ("B", "source_subsumes_target")],
        )

    def test_quantization_collapses_float_noise(self):
        # scores differing below the 1e-12 quantum are tied -> tie-break decides
        rows = [
            _row("B", "equivalent", 0.5),
            _row("A", "equivalent", 0.5 + 1e-13),
        ]
        ranked = metrics._rank_predictions_for_source(rows)
        # quantized equal -> lexicographic target A before B (not score 0.5+eps)
        self.assertEqual([r["TgtEntity"] for r in ranked], ["A", "B"])

    def test_dedup_keeps_max(self):
        rows = [_row("A", "equivalent", 0.2), _row("A", "equivalent", 0.9)]
        ranked = metrics._rank_predictions_for_source(rows)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(float(ranked[0]["Score"]), 0.9)

    def test_full_fixture_ranking_block_a(self):
        # the exact hand-traced ranking for the Q0 block
        preds = loaders.load_block_format_predictions(
            FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
            relations=DEFAULT_RELATIONS, candidate_count=4)
        block_a = [r for r in preds if r["SrcEntity"] == "SA"]
        ranked = metrics._rank_predictions_for_source(block_a)
        self.assertEqual(
            [(r["TgtEntity"], r["Relation"]) for r in ranked],
            [("X", "equivalent"), ("T", "equivalent"),
             ("T", "source_subsumed_by_target"),
             ("T", "source_subsumes_target"),
             ("P1", "source_subsumed_by_target"),
             ("C1", "source_subsumes_target"),
             ("P1", "equivalent"), ("C1", "equivalent"),
             ("C1", "source_subsumed_by_target"),
             ("P1", "source_subsumes_target"),
             ("X", "source_subsumed_by_target"),
             ("X", "source_subsumes_target")],
        )


class TestHierarchyAwareNdcg(unittest.TestCase):
    def test_query_a_value(self):
        ranked = [
            _row("X", "equivalent", 0.95), _row("T", "equivalent", 0.90),
            _row("T", "source_subsumed_by_target", 0.50),
            _row("T", "source_subsumes_target", 0.40),
            _row("P1", "source_subsumed_by_target", 0.30),
            _row("C1", "source_subsumes_target", 0.30),
        ]
        graded = {("T", "equivalent"): 1.0,
                  ("T", "source_subsumed_by_target"): 0.6,
                  ("T", "source_subsumes_target"): 0.6,
                  ("P1", "source_subsumed_by_target"): 0.3,
                  ("C1", "source_subsumes_target"): 0.3}
        self.assertAlmostEqual(
            metrics.hierarchy_aware_ndcg(ranked, graded, k=10), NDCG_A, places=12)

    def test_perfect_ranking_is_one(self):
        ranked = [_row("A", "equivalent", 0.9), _row("B", "equivalent", 0.5)]
        graded = {("A", "equivalent"): 1.0, ("B", "equivalent"): 0.5}
        self.assertAlmostEqual(metrics.hierarchy_aware_ndcg(ranked, graded), 1.0)

    def test_no_positive_gain_is_zero(self):
        ranked = [_row("A", "equivalent", 0.9)]
        self.assertEqual(metrics.hierarchy_aware_ndcg(ranked, {}, k=10), 0.0)


class _FixtureScored(unittest.TestCase):
    def setUp(self):
        self.preds = loaders.load_block_format_predictions(
            FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
            relations=DEFAULT_RELATIONS, candidate_count=4)
        self.answers = loaders.load_answers(FIXTURES / "answers.tsv")
        self.preferred = loaders.load_preferred_pairs(FIXTURES / "preferred.tsv")
        self.cands = loaders.load_per_query_candidate_sets_from_answers(
            FIXTURES / "answers.tsv")


class TestPreferredTypedMetrics(_FixtureScored):
    def test_values(self):
        m = metrics.score_preferred_typed_metrics(
            self.preds, self.preferred, self.cands)
        # A: preferred (T,eq) at rank 2; B: preferred (P1,ssbt) at rank 2
        self.assertAlmostEqual(m["preferred_typed_mrr"], 0.5)        # mean(.5,.5)
        self.assertAlmostEqual(m["preferred_typed_hits_at_1"], 0.0)
        self.assertAlmostEqual(m["preferred_typed_hits_at_5"], 1.0)
        self.assertAlmostEqual(m["preferred_typed_hits_at_10"], 1.0)
        self.assertAlmostEqual(m["median_preferred_typed_rank"], 2.0)
        self.assertEqual(m["preferred_pair_queries"], 2.0)

    def test_no_hit_yields_zero_rr(self):
        # a query whose preferred pair is outside the candidate filter
        preferred = {("SA", "Q0"): [("NOT_A_CANDIDATE", "equivalent")]}
        m = metrics.score_preferred_typed_metrics(
            self.preds, preferred, self.cands)
        self.assertEqual(m["preferred_typed_mrr"], 0.0)


class TestScoreTypedMetrics(_FixtureScored):
    def test_computed_from_hierarchy(self):
        m = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            hierarchy=chain_hierarchy(),
            per_query_candidate_sets=self.cands)
        self.assertEqual(m["queries"], 2.0)
        self.assertAlmostEqual(m["preferred_typed_mrr"], 0.5)
        self.assertAlmostEqual(
            m["hierarchy_aware_typed_ndcg_at_10"], (NDCG_A + NDCG_B) / 2,
            places=12)
        self.assertAlmostEqual(
            m["hierarchy_aware_typed_ndcg_at_10__equivalence_only"], NDCG_A,
            places=12)
        self.assertAlmostEqual(
            m["hierarchy_aware_typed_ndcg_at_10__subsumption_only"], NDCG_B,
            places=12)
        self.assertEqual(m["hierarchy_aware_typed_ndcg_at_10_queries"], 2.0)
        self.assertEqual(
            m["hierarchy_aware_typed_ndcg_at_10__equivalence_only_queries"], 1.0)
        self.assertEqual(
            m["hierarchy_aware_typed_ndcg_at_10__subsumption_only_queries"], 1.0)

    def test_consumed_graded_matches_computed(self):
        # the drift guard: consuming graded.tsv == recomputing from the hierarchy
        graded = loaders.load_graded_relevance(FIXTURES / "graded.tsv")
        from_file = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            graded_relevance=graded, per_query_candidate_sets=self.cands)
        from_hier = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            hierarchy=chain_hierarchy(), per_query_candidate_sets=self.cands)
        for key in ("hierarchy_aware_typed_ndcg_at_10",
                    "hierarchy_aware_typed_ndcg_at_10__equivalence_only",
                    "hierarchy_aware_typed_ndcg_at_10__subsumption_only"):
            self.assertAlmostEqual(from_file[key], from_hier[key], places=12)

    def test_omits_hndcg_without_graded_source(self):
        m = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            per_query_candidate_sets=self.cands)
        self.assertNotIn("hierarchy_aware_typed_ndcg_at_10", m)
        self.assertIn("preferred_typed_mrr", m)  # the rest still present

    def test_hierarchy_without_candidate_sets_raises(self):
        with self.assertRaises(ValueError):
            metrics.score_typed_metrics(
                self.preds, self.answers, self.preferred,
                hierarchy=chain_hierarchy())

    def test_deterministic(self):
        a = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            hierarchy=chain_hierarchy(), per_query_candidate_sets=self.cands)
        b = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            hierarchy=chain_hierarchy(), per_query_candidate_sets=self.cands)
        self.assertEqual(a, b)

    def test_preferred_typed_eq_sub_slices(self):
        m = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            per_query_candidate_sets=self.cands)
        # eq slice = query A only (preferred rank 2); sub slice = query B only
        self.assertAlmostEqual(m["preferred_typed_mrr__equivalence_only"], 0.5)
        self.assertAlmostEqual(m["preferred_typed_mrr__subsumption_only"], 0.5)
        self.assertAlmostEqual(
            m["preferred_typed_hits_at_1__equivalence_only"], 0.0)
        self.assertAlmostEqual(
            m["preferred_typed_hits_at_5__subsumption_only"], 1.0)
        self.assertAlmostEqual(
            m["median_preferred_typed_rank__equivalence_only"], 2.0)
        self.assertEqual(m["preferred_pair_queries__equivalence_only"], 1.0)
        self.assertEqual(m["preferred_pair_queries__subsumption_only"], 1.0)

    def test_eq_sub_partition_matches_gold_classification(self):
        # the preferred-relation partition (used for the preferred-typed slices)
        # must agree with the gold-relation classification (used for H-nDCG)
        eq_pairs, sub_pairs = metrics._partition_preferred_by_relation(
            self.preferred)
        for key, pairs in self.preferred.items():
            gold_is_sub = "equivalent" not in {r for _t, r in self.answers[key]}
            self.assertEqual(key in sub_pairs, gold_is_sub)
            self.assertEqual(key in eq_pairs, not gold_is_sub)


class TestQ0Q1NoContamination(unittest.TestCase):
    """
    one SrcEntity poses BOTH Q0 and Q1 over overlapping candidate pools. each
    query's ranking must use only its OWN rows — keying on (SrcEntity, QueryID),
    not SrcEntity alone. (regression: SrcEntity-only keying leaked Q0's high-
    scored rows into Q1's ranking and vice versa.)
    """

    def _predictions(self):
        # Q0: T is the eq match (0.9); Q1: U/ssbt is the sub match (0.8).
        # T scores LOW in Q1 — so leaking Q0's T=0.9 would wrongly top Q1.
        q0 = {("T", "equivalent"): 0.9,
              ("T", "source_subsumed_by_target"): 0.1,
              ("T", "source_subsumes_target"): 0.1,
              ("U", "equivalent"): 0.2,
              ("U", "source_subsumed_by_target"): 0.1,
              ("U", "source_subsumes_target"): 0.1}
        q1 = {("U", "source_subsumed_by_target"): 0.8,
              ("U", "equivalent"): 0.1,
              ("U", "source_subsumes_target"): 0.1,
              ("T", "equivalent"): 0.05,
              ("T", "source_subsumed_by_target"): 0.05,
              ("T", "source_subsumes_target"): 0.05}
        rows = []
        for qid, sc in (("Q0", q0), ("Q1", q1)):
            for (tgt, rel), s in sc.items():
                rows.append({"SrcEntity": "S", "QueryID": qid, "TgtEntity": tgt,
                             "Relation": rel, "Score": str(s)})
        return rows

    def setUp(self):
        self.preds = self._predictions()
        self.answers = {("S", "Q0"): {("T", "equivalent")},
                        ("S", "Q1"): {("U", "source_subsumed_by_target")}}
        self.preferred = {("S", "Q0"): [("T", "equivalent")],
                          ("S", "Q1"): [("U", "source_subsumed_by_target")]}
        self.cands = {("S", "Q0"): {"T", "U"}, ("S", "Q1"): {"T", "U"}}

    def test_preferred_pairs_rank_one_in_own_query(self):
        m = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            per_query_candidate_sets=self.cands)
        self.assertAlmostEqual(m["preferred_typed_mrr"], 1.0)
        self.assertAlmostEqual(m["preferred_typed_hits_at_1"], 1.0)

    def test_entity_only_not_contaminated(self):
        m = metrics.score_typed_metrics(
            self.preds, self.answers, self.preferred,
            per_query_candidate_sets=self.cands)
        self.assertAlmostEqual(m["entity_only_mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()
