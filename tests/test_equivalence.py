"""
Track 1 equivalence scoring: the pure metric core (set P/R/F1 + MRR/Hits@k) and the
loaders/report. The RDF loader needs rdflib (the `[rdf]` extra) and is skip-guarded.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oaei_bioml_eval.equivalence import metrics
from oaei_bioml_eval.equivalence.loaders import load_global_reference, load_pairs_rdf
from oaei_bioml_eval.equivalence.report import score_global_files, score_local_files
from oaei_bioml_eval.io import list_literal, write_tsv

try:
    import rdflib  # noqa: F401
    _HAS_RDFLIB = True
except ImportError:
    _HAS_RDFLIB = False

_ALIGNMENT_RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:align="http://knowledgeweb.semanticweb.org/heterogeneity/alignment#">
  <align:Alignment>
    <align:map><align:Cell>
      <align:entity1 rdf:resource="http://ex.org/A1"/>
      <align:entity2 rdf:resource="http://ex.org/B1"/>
      <align:relation>=</align:relation>
    </align:Cell></align:map>
    <align:map><align:Cell>
      <align:entity1 rdf:resource="http://ex.org/A2"/>
      <align:entity2 rdf:resource="http://ex.org/B2"/>
      <align:relation>=</align:relation>
    </align:Cell></align:map>
  </align:Alignment>
</rdf:RDF>
"""


# a reference with one kept `=` cell and one incoherence-causing `?` cell
_FLAGGED_RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:align="http://knowledgeweb.semanticweb.org/heterogeneity/alignment#">
  <align:Alignment>
    <align:map><align:Cell>
      <align:entity1 rdf:resource="http://ex.org/A1"/>
      <align:entity2 rdf:resource="http://ex.org/B1"/>
      <align:relation>=</align:relation>
    </align:Cell></align:map>
    <align:map><align:Cell>
      <align:entity1 rdf:resource="http://ex.org/A2"/>
      <align:entity2 rdf:resource="http://ex.org/B2"/>
      <align:relation>?</align:relation>
    </align:Cell></align:map>
  </align:Alignment>
</rdf:RDF>
"""


class TestGlobalPRF1(unittest.TestCase):
    def test_set_prf1(self):
        m = metrics.global_prf1({("a", "b"), ("a", "c")}, {("a", "b"), ("a", "d")})
        self.assertEqual((m["precision"], m["recall"], m["f1"]), (0.5, 0.5, 0.5))
        self.assertEqual((m["true_positive"], m["predicted"], m["reference"]), (1.0, 2.0, 2.0))

    def test_empty_sets_are_zero_not_error(self):
        m = metrics.global_prf1(set(), {("a", "b")})
        self.assertEqual((m["precision"], m["recall"], m["f1"]), (0.0, 0.0, 0.0))

    def test_many_to_many_reference(self):
        # a source mapping to two targets, both predicted -> full recall on that source
        m = metrics.global_prf1({("a", "b"), ("a", "c")}, {("a", "b"), ("a", "c"), ("x", "y")})
        self.assertEqual(m["recall"], 2 / 3)
        self.assertEqual(m["precision"], 1.0)

    def test_score_global_files_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "sub.tsv", [{"SrcEntity": "a", "TgtEntity": "b"},
                                        {"SrcEntity": "a", "TgtEntity": "z"}], ["SrcEntity", "TgtEntity"])
            write_tsv(tmp / "ref.tsv", [{"SrcEntity": "a", "TgtEntity": "b"}], ["SrcEntity", "TgtEntity"])
            m = score_global_files(tmp / "sub.tsv", tmp / "ref.tsv")
            self.assertEqual((m["precision"], m["recall"]), (0.5, 1.0))


class TestCoherenceAwarePRF1(unittest.TestCase):
    """LargeBio `?`-flagged P/R/F1: the flagged subset leaves both denominators"""

    def test_flagged_leaves_both_denominators(self):
        # R+ = {(a,b)}; predicted {(a,b) hit, (a,u) a `?` ignored, (a,x) a wrong}.
        # P = 1 / |A - U| = 1/2 (the `?` predicted leaves A); R = 1 / |R+| = 1/1.
        ref = {("a", "b"), ("a", "u")}
        flagged = {("a", "u")}
        m = metrics.global_prf1_coherence_aware({("a", "b"), ("a", "u"), ("a", "x")}, ref, flagged)
        self.assertEqual(m["precision_coherent"], 0.5)
        self.assertEqual(m["recall_coherent"], 1.0)
        self.assertEqual(m["true_positive_coherent"], 1.0)
        self.assertEqual((m["reference_positive"], m["reference_flagged"]), (1.0, 1.0))
        self.assertEqual(m["predicted_flagged"], 1.0)

    def test_empty_flag_set_reduces_to_standard(self):
        predicted, ref = {("a", "b"), ("a", "z")}, {("a", "b")}
        standard = metrics.global_prf1(predicted, ref)
        coherent = metrics.global_prf1_coherence_aware(predicted, ref, set())
        self.assertEqual(coherent["precision_coherent"], standard["precision"])
        self.assertEqual(coherent["recall_coherent"], standard["recall"])
        self.assertEqual(coherent["f1_coherent"], standard["f1"])

    def test_all_reference_flagged_is_zero_not_error(self):
        m = metrics.global_prf1_coherence_aware({("a", "b")}, {("a", "b")}, {("a", "b")})
        self.assertEqual((m["precision_coherent"], m["recall_coherent"], m["f1_coherent"]), (0.0, 0.0, 0.0))

    def test_keys_disjoint_from_standard(self):
        standard = set(metrics.global_prf1(set(), set()))
        coherent = set(metrics.global_prf1_coherence_aware(set(), set(), set()))
        self.assertTrue(standard.isdisjoint(coherent))   # never overload precision/recall/f1

    def test_new_counts_are_count_metrics(self):
        # the four `_coherent`/reference_* counts must SUM (not mean) in macro
        for key in ("true_positive_coherent", "reference_positive", "reference_flagged", "predicted_flagged"):
            self.assertIn(key, metrics._COUNT_METRICS)


@unittest.skipUnless(_HAS_RDFLIB, "rdflib not installed")
class TestRdfLoader(unittest.TestCase):
    def test_alignment_rdf_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "align.rdf"
            path.write_text(_ALIGNMENT_RDF, encoding="utf-8")
            self.assertEqual(load_pairs_rdf(path),
                             {("http://ex.org/A1", "http://ex.org/B1"), ("http://ex.org/A2", "http://ex.org/B2")})

    def test_mixed_typed_and_untyped_cells_both_load(self):
        # a typed <align:Cell> + an untyped cell carrying only align:entity1 — union, not `or`,
        # so neither set is dropped (regression: `or` returned just the first non-empty set)
        mixed = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:align="http://knowledgeweb.semanticweb.org/heterogeneity/alignment#">
  <align:Alignment>
    <align:map><align:Cell>
      <align:entity1 rdf:resource="http://ex.org/A1"/>
      <align:entity2 rdf:resource="http://ex.org/B1"/>
      <align:relation>=</align:relation>
    </align:Cell></align:map>
    <align:map><rdf:Description>
      <align:entity1 rdf:resource="http://ex.org/A2"/>
      <align:entity2 rdf:resource="http://ex.org/B2"/>
      <align:relation>=</align:relation>
    </rdf:Description></align:map>
  </align:Alignment>
</rdf:RDF>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.rdf"
            path.write_text(mixed, encoding="utf-8")
            self.assertEqual(load_pairs_rdf(path),
                             {("http://ex.org/A1", "http://ex.org/B1"), ("http://ex.org/A2", "http://ex.org/B2")})
            reference, _flagged = load_global_reference(path)
            self.assertEqual(len(reference), 2)   # both the typed and the untyped cell

    def test_load_global_reference_splits_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ref.rdf"
            path.write_text(_FLAGGED_RDF, encoding="utf-8")
            reference, flagged = load_global_reference(path)
        a1b1, a2b2 = ("http://ex.org/A1", "http://ex.org/B1"), ("http://ex.org/A2", "http://ex.org/B2")
        self.assertEqual(reference, {a1b1, a2b2})   # R = all cells, `?` still a positive
        self.assertEqual(flagged, {a2b2})           # U = the `?` cell only

    def test_score_global_files_emits_both_families_with_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ref.rdf").write_text(_FLAGGED_RDF, encoding="utf-8")
            # a submission that predicts the kept `=` AND the flagged `?` cell
            (tmp / "sub.rdf").write_text(_ALIGNMENT_RDF, encoding="utf-8")
            m = score_global_files(tmp / "sub.rdf", tmp / "ref.rdf")
        # standard: 2 predicted, both in R -> P=R=F1=1.0 (`?` counts as a positive)
        self.assertEqual((m["precision"], m["recall"], m["f1"]), (1.0, 1.0, 1.0))
        # coherent: the `?` prediction leaves A (denom 1), TP=1, R+=1 -> P=R=1.0
        self.assertEqual((m["precision_coherent"], m["recall_coherent"]), (1.0, 1.0))
        self.assertEqual((m["reference_flagged"], m["predicted_flagged"]), (1.0, 1.0))

    def test_load_global_reference_tsv_has_no_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "ref.tsv", [{"SrcEntity": "a", "TgtEntity": "b"}], ["SrcEntity", "TgtEntity"])
            reference, flagged = load_global_reference(tmp / "ref.tsv")
        self.assertEqual((reference, flagged), ({("a", "b")}, set()))   # TSV cannot carry `?`


class TestLocalRanking(unittest.TestCase):
    def test_mrr_and_hits(self):
        rankings = {0: ["x", "gold", "y"], 1: ["gold", "p", "q"]}   # ranks 2 and 1
        golds = {0: "gold", 1: "gold"}
        m = metrics.local_ranking_metrics(rankings, golds)
        self.assertEqual(m["mrr"], (0.5 + 1.0) / 2)
        self.assertEqual(m["hits_at_1"], 0.5)
        self.assertEqual(m["hits_at_5"], 1.0)
        self.assertEqual(m["queries"], 2.0)

    def test_gold_absent_is_a_miss(self):
        m = metrics.local_ranking_metrics({0: ["x", "y"]}, {0: "gold"})
        self.assertEqual((m["mrr"], m["hits_at_1"]), (0.0, 0.0))

    def test_score_local_files_list_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "sub.tsv", [
                {"SrcEntity": "s1", "TgtCandidates": list_literal(["c", "g1", "d"])},   # gold rank 2
                {"SrcEntity": "s2", "TgtCandidates": list_literal(["g2", "e", "f"])},   # gold rank 1
            ], ["SrcEntity", "TgtCandidates"])
            write_tsv(tmp / "gold.tsv", [{"SrcEntity": "s1", "TgtEntity": "g1"},
                                         {"SrcEntity": "s2", "TgtEntity": "g2"}], ["SrcEntity", "TgtEntity"])
            m = score_local_files(tmp / "sub.tsv", tmp / "gold.tsv")
            self.assertEqual(m["mrr"], (0.5 + 1.0) / 2)
            self.assertEqual(m["hits_at_1"], 0.5)

    def test_score_local_files_scored_block_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # two queries, 3 scored candidates each (canonical block order); gold wins q1, loses q2
            write_tsv(tmp / "sub.tsv", [
                {"SrcEntity": "s1", "TgtEntity": "g1", "Score": "0.9"},
                {"SrcEntity": "s1", "TgtEntity": "c", "Score": "0.5"},
                {"SrcEntity": "s1", "TgtEntity": "d", "Score": "0.1"},
                {"SrcEntity": "s2", "TgtEntity": "e", "Score": "0.9"},
                {"SrcEntity": "s2", "TgtEntity": "g2", "Score": "0.5"},
                {"SrcEntity": "s2", "TgtEntity": "f", "Score": "0.1"},
            ], ["SrcEntity", "TgtEntity", "Score"])
            write_tsv(tmp / "gold.tsv", [{"SrcEntity": "s1", "TgtEntity": "g1"},
                                         {"SrcEntity": "s2", "TgtEntity": "g2"}], ["SrcEntity", "TgtEntity"])
            m = score_local_files(tmp / "sub.tsv", tmp / "gold.tsv", candidate_count=3)
            self.assertEqual(m["mrr"], (1.0 + 0.5) / 2)   # g1 rank 1, g2 rank 2

    def test_block_spanning_two_sources_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "sub.tsv", [   # cc=3 but the first block leaks an s2 row -> misaligned
                {"SrcEntity": "s1", "TgtEntity": "g1", "Score": "0.9"},
                {"SrcEntity": "s1", "TgtEntity": "c", "Score": "0.5"},
                {"SrcEntity": "s2", "TgtEntity": "x", "Score": "0.1"},
            ], ["SrcEntity", "TgtEntity", "Score"])
            write_tsv(tmp / "gold.tsv", [{"SrcEntity": "s1", "TgtEntity": "g1"}], ["SrcEntity", "TgtEntity"])
            with self.assertRaises(ValueError):
                score_local_files(tmp / "sub.tsv", tmp / "gold.tsv", candidate_count=3)

    def test_reordered_submission_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "sub.tsv", [   # rows in the WRONG order vs the gold
                {"SrcEntity": "s2", "TgtCandidates": list_literal(["g2"])},
                {"SrcEntity": "s1", "TgtCandidates": list_literal(["g1"])},
            ], ["SrcEntity", "TgtCandidates"])
            write_tsv(tmp / "gold.tsv", [{"SrcEntity": "s1", "TgtEntity": "g1"},
                                         {"SrcEntity": "s2", "TgtEntity": "g2"}], ["SrcEntity", "TgtEntity"])
            with self.assertRaises(ValueError):
                score_local_files(tmp / "sub.tsv", tmp / "gold.tsv", candidate_count=1)

    @unittest.skipUnless(_HAS_RDFLIB, "rdflib not installed")
    def test_global_score_with_rdf_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "sub.rdf").write_text(_ALIGNMENT_RDF, encoding="utf-8")
            (tmp / "ref.rdf").write_text(_ALIGNMENT_RDF, encoding="utf-8")
            m = score_global_files(tmp / "sub.rdf", tmp / "ref.rdf")   # reference dispatched as RDF too
            self.assertEqual((m["precision"], m["recall"], m["f1"]), (1.0, 1.0, 1.0))

    def test_query_count_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_tsv(tmp / "sub.tsv", [{"SrcEntity": "s1", "TgtCandidates": list_literal(["g1"])}],
                      ["SrcEntity", "TgtCandidates"])
            write_tsv(tmp / "gold.tsv", [{"SrcEntity": "s1", "TgtEntity": "g1"},
                                         {"SrcEntity": "s2", "TgtEntity": "g2"}], ["SrcEntity", "TgtEntity"])
            with self.assertRaises(ValueError):
                score_local_files(tmp / "sub.tsv", tmp / "gold.tsv")


class TestMacro(unittest.TestCase):
    def test_macro_means_rates_sums_counts(self):
        macro = metrics.macro_average_across_tasks({
            "NCIT-DOID": {"f1": 0.8, "queries": 10.0},
            "SNOMED-FMA": {"f1": 0.6, "queries": 20.0},
        })
        self.assertAlmostEqual(macro["f1"], 0.7)
        self.assertEqual(macro["queries"], 30.0)


if __name__ == "__main__":
    unittest.main()
