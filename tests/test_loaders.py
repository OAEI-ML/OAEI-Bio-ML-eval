"""
loaders: the gold readers + the block-format submission loader, including its
fatal/warn/silent-filter edge cases. block edge cases are exercised on a tiny
single-query answers file (2 candidates x 3 relations = block size 6).
"""
from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from oaei_bioml_eval.typed import loaders
from oaei_bioml_eval.typed.metrics import DEFAULT_RELATIONS

from _fixtures import FIXTURES, write_tsv


class TestGoldLoaders(unittest.TestCase):
    def test_load_answers(self):
        answers = loaders.load_answers(FIXTURES / "answers.tsv")
        self.assertEqual(answers[("SA", "Q0")], {("T", "equivalent")})
        self.assertEqual(
            answers[("SB", "Q1")], {("P1", "source_subsumed_by_target")}
        )

    def test_load_candidate_sets_from_answers(self):
        cands = loaders.load_per_query_candidate_sets_from_answers(
            FIXTURES / "answers.tsv"
        )
        self.assertEqual(cands[("SA", "Q0")], {"T", "P1", "C1", "X"})
        self.assertEqual(cands[("SB", "Q1")], {"P1", "P2", "C1", "Y"})

    def test_load_preferred_pairs(self):
        pref = loaders.load_preferred_pairs(FIXTURES / "preferred.tsv")
        self.assertEqual(pref[("SA", "Q0")], [("T", "equivalent")])
        self.assertEqual(
            pref[("SB", "Q1")], [("P1", "source_subsumed_by_target")]
        )

    def test_load_preferred_pairs_missing_file_is_empty(self):
        self.assertEqual(loaders.load_preferred_pairs(FIXTURES / "nope.tsv"), {})

    def test_load_graded_relevance(self):
        graded = loaders.load_graded_relevance(FIXTURES / "graded.tsv")
        self.assertIsNotNone(graded)
        self.assertEqual(graded[("SA", "Q0")][("T", "equivalent")], 1.0)
        self.assertEqual(
            graded[("SB", "Q1")][("P2", "source_subsumed_by_target")], 0.5
        )

    def test_load_graded_relevance_missing_file_is_none(self):
        self.assertIsNone(loaders.load_graded_relevance(FIXTURES / "nope.tsv"))

    def test_load_answers_list_form(self):
        # the list column form lets a 1:many eq query carry several gold pairs
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.tsv"
            write_tsv(path, ["SrcEntity", "QueryID", "TgtEntities", "Relations"],
                      [{"SrcEntity": "S", "QueryID": "Q0",
                        "TgtEntities": "['A', 'B']",
                        "Relations": "['equivalent', 'equivalent']"}])
            answers = loaders.load_answers(path)
            self.assertEqual(
                answers[("S", "Q0")],
                {("A", "equivalent"), ("B", "equivalent")},
            )

    def test_load_answers_length_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.tsv"
            write_tsv(path, ["SrcEntity", "TgtEntities", "Relations"],
                      [{"SrcEntity": "S", "TgtEntities": "['A', 'B']",
                        "Relations": "['equivalent']"}])
            with self.assertRaises(ValueError):
                loaders.load_answers(path)


class TestBlockFormatHappyPath(unittest.TestCase):
    def setUp(self):
        self.rows = loaders.load_block_format_predictions(
            FIXTURES / "submission.block.tsv", FIXTURES / "answers.tsv",
            relations=DEFAULT_RELATIONS, candidate_count=4,
        )

    def test_recovers_query_ids_positionally(self):
        # block 0 -> answers row 0 (SA/Q0); block 1 -> SB/Q1
        by_src = {}
        for r in self.rows:
            by_src.setdefault(r["SrcEntity"], set()).add(r["QueryID"])
        self.assertEqual(by_src, {"SA": {"Q0"}, "SB": {"Q1"}})

    def test_emits_full_cartesian_product(self):
        # 2 queries x 4 candidates x 3 relations = 24 enriched rows
        self.assertEqual(len(self.rows), 24)
        keys = {(r["SrcEntity"], r["TgtEntity"], r["Relation"]) for r in self.rows}
        self.assertEqual(len(keys), 24)

    def test_scores_propagated(self):
        lut = {
            (r["SrcEntity"], r["TgtEntity"], r["Relation"]): float(r["Score"])
            for r in self.rows
        }
        self.assertAlmostEqual(lut[("SA", "T", "equivalent")], 0.90)
        self.assertAlmostEqual(lut[("SA", "X", "equivalent")], 0.95)
        self.assertAlmostEqual(
            lut[("SB", "P1", "source_subsumed_by_target")], 0.50
        )


class _TinyBlock(unittest.TestCase):
    """answers with one query: candidates [A1, A2], block size 6."""

    def _answers(self, tmp: str) -> Path:
        path = Path(tmp) / "answers.tsv"
        write_tsv(path,
                  ["SrcEntity", "QueryID", "TgtEntity", "Relation",
                   "TgtCandidates"],
                  [{"SrcEntity": "S", "QueryID": "Q0", "TgtEntity": "A1",
                    "Relation": "equivalent",
                    "TgtCandidates": "['A1', 'A2']"}])
        return path

    def _submission(self, tmp: str, rows: list[dict]) -> Path:
        path = Path(tmp) / "sub.tsv"
        write_tsv(path, ["SrcEntity", "TgtEntity", "Relation", "Score"], rows)
        return path

    def _full_block(self) -> list[dict]:
        rows = []
        for tgt in ("A1", "A2"):
            for rel in DEFAULT_RELATIONS:
                rows.append({"SrcEntity": "S", "TgtEntity": tgt,
                             "Relation": rel, "Score": "0.5"})
        return rows


class TestBlockFatals(_TinyBlock):
    def test_row_count_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            sub = self._submission(tmp, self._full_block()[:5])  # 5 != 6
            with self.assertRaises(ValueError):
                loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)

    def test_src_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            rows[0]["SrcEntity"] = "WRONG"
            sub = self._submission(tmp, rows)
            with self.assertRaises(ValueError):
                loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)

    def test_invalid_relation_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            rows[0]["Relation"] = "not_a_relation"
            sub = self._submission(tmp, rows)
            with self.assertRaises(ValueError):
                loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)

    def test_unparseable_score_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            rows[0]["Score"] = "high"
            sub = self._submission(tmp, rows)
            with self.assertRaises(ValueError):
                loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)


class TestBlockWarnsAndFilters(_TinyBlock):
    def test_non_candidate_silently_filtered_and_missing_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            # swap (A2, equivalent) for a non-candidate target Z
            rows[3] = {"SrcEntity": "S", "TgtEntity": "Z",
                       "Relation": "equivalent", "Score": "0.9"}
            sub = self._submission(tmp, rows)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                enriched = loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)
            # Z never appears; canonical product still has 6 rows
            self.assertEqual(len(enriched), 6)
            self.assertNotIn(
                "Z", {r["TgtEntity"] for r in enriched}
            )
            # the displaced (A2, equivalent) is filled with 0.0
            lut = {(r["TgtEntity"], r["Relation"]): float(r["Score"])
                   for r in enriched}
            self.assertEqual(lut[("A2", "equivalent")], 0.0)
            self.assertTrue(any("missing" in str(w.message) for w in caught))

    def test_duplicate_pair_keeps_max_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            # duplicate (A1, equivalent); drop (A2, equivalent) to keep count=6
            rows[0]["Score"] = "0.3"
            rows[3] = {"SrcEntity": "S", "TgtEntity": "A1",
                       "Relation": "equivalent", "Score": "0.95"}
            sub = self._submission(tmp, rows)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                enriched = loaders.load_block_format_predictions(
                    sub, answers, DEFAULT_RELATIONS, candidate_count=2)
            lut = {(r["TgtEntity"], r["Relation"]): float(r["Score"])
                   for r in enriched}
            self.assertEqual(lut[("A1", "equivalent")], 0.95)  # max kept
            self.assertTrue(any("duplicate" in str(w.message) for w in caught))


class TestBlockPrecisionAndRobustness(_TinyBlock):
    def test_sub_6dp_gap_survives_reemit(self):
        # the re-emit must not truncate below the metrics quantization (12 dp);
        # a 1e-8 gap between two candidates has to be preserved
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            rows = self._full_block()
            for r in rows:
                if r["Relation"] == "equivalent" and r["TgtEntity"] == "A1":
                    r["Score"] = "0.50000001"
                if r["Relation"] == "equivalent" and r["TgtEntity"] == "A2":
                    r["Score"] = "0.50000002"
            sub = self._submission(tmp, rows)
            enriched = loaders.load_block_format_predictions(
                sub, answers, DEFAULT_RELATIONS, candidate_count=2)
            lut = {(r["TgtEntity"], r["Relation"]): float(r["Score"])
                   for r in enriched}
            self.assertEqual(lut[("A1", "equivalent")], 0.50000001)
            self.assertEqual(lut[("A2", "equivalent")], 0.50000002)

    def test_non_finite_score_raises(self):
        for bad in ("nan", "inf", "-inf"):
            with tempfile.TemporaryDirectory() as tmp:
                answers = self._answers(tmp)
                rows = self._full_block()
                rows[0]["Score"] = bad
                sub = self._submission(tmp, rows)
                with self.assertRaises(ValueError):
                    loaders.load_block_format_predictions(
                        sub, answers, DEFAULT_RELATIONS, candidate_count=2)

    def test_loose_mode_coalesces_short_row(self):
        # a short row leaves csv.DictReader's None; loose mode must not forward
        # it (the ranker would choke). 6 rows total to clear the count gate.
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            sub = Path(tmp) / "sub.tsv"
            with sub.open("w", encoding="utf-8", newline="") as h:
                h.write("SrcEntity\tTgtEntity\tRelation\tScore\n")
                h.write("S\tA1\tequivalent\n")           # short -> Score is None
                for tgt in ("A1", "A2"):
                    for rel in DEFAULT_RELATIONS:
                        if not (tgt == "A1" and rel == "equivalent"):
                            h.write(f"S\t{tgt}\t{rel}\t0.5\n")
            enriched = loaders.load_block_format_predictions(
                sub, answers, DEFAULT_RELATIONS, candidate_count=2, strict=False)
            for r in enriched:
                self.assertIsNotNone(r["Score"])
                self.assertIsNotNone(r["TgtEntity"])


if __name__ == "__main__":
    unittest.main()
