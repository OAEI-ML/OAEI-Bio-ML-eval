"""
fixture-free tests for the typed graded-relevance gain function + preferred-pair
selection. small hand-built hierarchy, exact expected gains.

run with:  python -m unittest discover -s tests
"""
import unittest

from oaei_bioml_eval.hierarchy import HierarchyIndex
from oaei_bioml_eval.typed import relevance


def _chain_hierarchy() -> HierarchyIndex:
    # P2 -> P1 -> T -> C1 -> C2   (parent_id is the broader class)
    return HierarchyIndex([
        {"child_id": "T", "parent_id": "P1"},
        {"child_id": "P1", "parent_id": "P2"},
        {"child_id": "C1", "parent_id": "T"},
        {"child_id": "C2", "parent_id": "C1"},
    ])


class TestHierarchyDistances(unittest.TestCase):
    def test_ancestors_and_descendants(self):
        h = _chain_hierarchy()
        self.assertEqual(h.ancestors_with_distance("T", 3), {"P1": 1, "P2": 2})
        self.assertEqual(h.descendants_with_distance("T", 3), {"C1": 1, "C2": 2})

    def test_distance_horizon(self):
        h = _chain_hierarchy()
        self.assertEqual(h.ancestors_with_distance("T", 1), {"P1": 1})
        self.assertEqual(h.ancestors_with_distance("T", 0), {})


class TestEquivalenceGains(unittest.TestCase):
    def setUp(self):
        self.h = _chain_hierarchy()
        self.cands = {"T", "P1", "P2", "C1", "C2", "X"}

    def test_full_eq_gain_table(self):
        gains = relevance.compute_graded_relevance(("T", "equivalent"), self.cands, self.h)
        self.assertAlmostEqual(gains[("T", "equivalent")], 1.0)
        self.assertAlmostEqual(gains[("T", "source_subsumed_by_target")], 0.6)
        self.assertAlmostEqual(gains[("T", "source_subsumes_target")], 0.6)
        self.assertAlmostEqual(gains[("P1", "source_subsumed_by_target")], 0.6 / 2)
        self.assertAlmostEqual(gains[("P2", "source_subsumed_by_target")], 0.6 / 3)
        self.assertAlmostEqual(gains[("C1", "source_subsumes_target")], 0.6 / 2)
        self.assertAlmostEqual(gains[("C2", "source_subsumes_target")], 0.6 / 3)
        self.assertEqual(len(gains), 7)               # X earns nothing
        self.assertTrue(all(0.0 < g <= 1.0 for g in gains.values()))

    def test_exact_target_absent_from_candidates(self):
        # T not a candidate -> no exact / no biconditional, but ancestors still credited
        gains = relevance.compute_graded_relevance(("T", "equivalent"), {"P1"}, self.h)
        self.assertEqual(gains, {("P1", "source_subsumed_by_target"): 0.6 / 2})


class TestSubsumptionGains(unittest.TestCase):
    def setUp(self):
        self.h = _chain_hierarchy()
        self.cands = {"T", "P1", "P2", "C1", "C2"}

    def test_ssbt_only_ancestors(self):
        gains = relevance.compute_graded_relevance(("T", "source_subsumed_by_target"), self.cands, self.h)
        self.assertEqual(
            gains,
            {
                ("T", "source_subsumed_by_target"): 1.0,
                ("P1", "source_subsumed_by_target"): 1.0 / 2,
                ("P2", "source_subsumed_by_target"): 1.0 / 3,
            },
        )

    def test_sst_only_descendants(self):
        gains = relevance.compute_graded_relevance(("T", "source_subsumes_target"), self.cands, self.h)
        self.assertEqual(
            gains,
            {
                ("T", "source_subsumes_target"): 1.0,
                ("C1", "source_subsumes_target"): 1.0 / 2,
                ("C2", "source_subsumes_target"): 1.0 / 3,
            },
        )


class TestSelectPreferredPairs(unittest.TestCase):
    def test_equivalence_precedence(self):
        gold = {("A", "equivalent"), ("B", "source_subsumed_by_target")}
        self.assertEqual(relevance.select_preferred_pairs(gold), [("A", "equivalent")])

    def test_subsumption_direction_precedence(self):
        gold = {("B", "source_subsumed_by_target"), ("C", "source_subsumes_target")}
        self.assertEqual(relevance.select_preferred_pairs(gold), [("B", "source_subsumed_by_target")])

    def test_one_to_many_equivalence_sorted(self):
        gold = {("A2", "equivalent"), ("A1", "equivalent")}
        self.assertEqual(relevance.select_preferred_pairs(gold), [("A1", "equivalent"), ("A2", "equivalent")])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            relevance.select_preferred_pairs(set())


if __name__ == "__main__":
    unittest.main()
