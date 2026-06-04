"""round-trip tests for the vendored io helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oaei_bioml_eval import io


class TestTsv(unittest.TestCase):
    def test_round_trip(self):
        rows = [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.tsv"
            io.write_tsv(path, rows)
            self.assertEqual(io.read_tsv(path), rows)

    def test_write_tsv_fills_missing_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.tsv"
            io.write_tsv(path, [{"a": "1"}], fieldnames=["a", "b"])
            self.assertEqual(io.read_tsv(path), [{"a": "1", "b": ""}])


class TestJson(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            io.write_json(path, {"x": 1.5, "y": [1, 2]})
            self.assertEqual(io.read_json(path), {"x": 1.5, "y": [1, 2]})


class TestListLiteral(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(io.parse_list(io.list_literal(["a", "b"])), ["a", "b"])

    def test_empty_is_empty_list(self):
        self.assertEqual(io.parse_list(""), [])
        self.assertEqual(io.parse_list(None), [])

    def test_coerces_to_str(self):
        self.assertEqual(io.parse_list("[1, 2]"), ["1", "2"])

    def test_non_list_raises(self):
        with self.assertRaises(ValueError):
            io.parse_list("{'a': 1}")


if __name__ == "__main__":
    unittest.main()
