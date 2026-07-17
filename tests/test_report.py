"""
the file-level entry point + the cross-task macro. score_files is exercised
end-to-end on the fixture, across its graded-source variants; the macro test
checks the rate-mean / count-sum split.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oaei_bioml_eval import io
from oaei_bioml_eval.typed import report

from _fixtures import FIXTURES, write_tsv

NDCG_A = 0.7340895391998685
NDCG_B = 0.6696718164942300


def _score(**kwargs):
    return report.score_files(
        FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
        candidate_count=4, **kwargs)


class TestScoreFiles(unittest.TestCase):
    def test_headline_keys_present(self):
        m = _score(hierarchy_path=FIXTURES / "hierarchy.tsv")
        for key in ("preferred_typed_mrr", "hierarchy_aware_typed_ndcg_at_10",
                    "preferred_typed_hits_at_1", "median_preferred_typed_rank",
                    "entity_only_mrr", "relation_accuracy_on_preferred_entity",
                    "queries"):
            self.assertIn(key, m)

    def test_values_with_hierarchy(self):
        m = _score(hierarchy_path=FIXTURES / "hierarchy.tsv")
        self.assertAlmostEqual(m["preferred_typed_mrr"], 0.5)
        self.assertAlmostEqual(
            m["hierarchy_aware_typed_ndcg_at_10"], (NDCG_A + NDCG_B) / 2,
            places=12)
        self.assertAlmostEqual(m["entity_only_mrr"], 0.75)
        self.assertAlmostEqual(m["relation_accuracy_on_preferred_entity"], 0.5)

    def test_derived_preferred_matches_explicit(self):
        derived = _score(hierarchy_path=FIXTURES / "hierarchy.tsv")
        explicit = _score(hierarchy_path=FIXTURES / "hierarchy.tsv",
                          preferred_pairs_path=FIXTURES / "preferred.tsv")
        self.assertEqual(derived, explicit)

    def test_graded_path_matches_hierarchy_path(self):
        from_graded = _score(graded_relevance_path=FIXTURES / "graded.tsv")
        from_hier = _score(hierarchy_path=FIXTURES / "hierarchy.tsv")
        self.assertAlmostEqual(
            from_graded["hierarchy_aware_typed_ndcg_at_10"],
            from_hier["hierarchy_aware_typed_ndcg_at_10"], places=12)

    def test_no_graded_source_omits_hndcg(self):
        m = _score()
        self.assertNotIn("hierarchy_aware_typed_ndcg_at_10", m)

    def test_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scores.json"
            m = _score(hierarchy_path=FIXTURES / "hierarchy.tsv", output_path=out)
            self.assertEqual(io.read_json(out), m)

    def test_default_candidate_count_mismatch_raises(self):
        # the fixture is 4 candidates; the default 50 -> block size 150 -> count off
        with self.assertRaises(ValueError):
            report.score_files(
                FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
                hierarchy_path=FIXTURES / "hierarchy.tsv")

    def test_bad_format_raises(self):
        with self.assertRaises(ValueError):
            report.score_files(
                FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
                submission_format="xml", candidate_count=4)


class TestMacroAverageAcrossTasks(unittest.TestCase):
    def test_rate_mean_and_count_sum(self):
        results = {
            "NCIT-DOID/lex": {"preferred_typed_mrr": 0.6, "queries": 10.0,
                              "preferred_pair_queries": 8.0},
            "SNOMED-FMA/lex": {"preferred_typed_mrr": 0.4, "queries": 20.0,
                               "preferred_pair_queries": 12.0},
            "NCIT-DOID/sap": {"preferred_typed_mrr": 0.8, "queries": 10.0},
        }
        agg = report.macro_average_across_tasks(results)

        lex = agg["lex"]
        self.assertAlmostEqual(lex["preferred_typed_mrr"], 0.5)   # mean(.6,.4)
        self.assertEqual(lex["queries_sum"], 30.0)
        self.assertEqual(lex["queries_mean"], 15.0)
        self.assertEqual(lex["queries"], 30.0)                    # bare == _sum
        self.assertEqual(lex["preferred_pair_queries_sum"], 20.0)
        self.assertEqual(lex["tasks"], 2.0)

        sap = agg["sap"]
        self.assertAlmostEqual(sap["preferred_typed_mrr"], 0.8)
        self.assertEqual(sap["queries"], 10.0)
        self.assertEqual(sap["tasks"], 1.0)

    def test_ignores_keys_without_separator(self):
        agg = report.macro_average_across_tasks({"no_separator": {"x": 1.0}})
        self.assertEqual(agg, {})

    def test_selectable_micro_weights_by_contributing_queries(self):
        results = {
            "small/lex": {
                "preferred_typed_mrr": 1.0,
                "preferred_pair_queries": 1.0,
                "queries": 1.0,
            },
            "large/lex": {
                "preferred_typed_mrr": 0.0,
                "preferred_pair_queries": 9.0,
                "queries": 9.0,
            },
        }
        macro = report.aggregate_across_tasks(results, average="macro")["lex"]
        micro = report.aggregate_across_tasks(results, average="micro")["lex"]
        self.assertEqual(macro["preferred_typed_mrr"], 0.5)
        self.assertEqual(micro["preferred_typed_mrr"], 0.1)
        self.assertEqual(micro["queries"], 10.0)
        self.assertEqual(micro["tasks"], 2.0)


class TestPrecisionEndToEnd(unittest.TestCase):
    def test_sub_6dp_gap_decides_rank_one(self):
        # gold A2 truly out-ranks A1 by 1e-8; the headline rank-1 metrics must
        # honour it end to end (regression: loader truncated to 6 dp -> tie ->
        # lexicographic A1 < A2 wrongly demoted A2 to rank 2)
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp) / "answers.tsv"
            write_tsv(answers,
                      ["SrcEntity", "QueryID", "TgtEntity", "Relation",
                       "TgtCandidates"],
                      [{"SrcEntity": "S", "QueryID": "Q0", "TgtEntity": "A2",
                        "Relation": "equivalent",
                        "TgtCandidates": "['A1', 'A2']"}])
            scores = {("A1", "equivalent"): "0.50000001",
                      ("A2", "equivalent"): "0.50000002"}
            rows = []
            for tgt in ("A1", "A2"):
                for rel in ("equivalent", "source_subsumed_by_target",
                            "source_subsumes_target"):
                    rows.append({"SrcEntity": "S", "TgtEntity": tgt,
                                 "Relation": rel,
                                 "Score": scores.get((tgt, rel), "0.0")})
            sub = Path(tmp) / "sub.tsv"
            write_tsv(sub, ["SrcEntity", "TgtEntity", "Relation", "Score"], rows)
            m = report.score_files(sub, answers, candidate_count=2)
            self.assertAlmostEqual(m["preferred_typed_mrr"], 1.0)
            self.assertAlmostEqual(m["preferred_typed_hits_at_1"], 1.0)


if __name__ == "__main__":
    unittest.main()
