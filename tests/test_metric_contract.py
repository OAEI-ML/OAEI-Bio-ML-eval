"""Byte-stable metric baselines guarding the Java-free coherence migration."""

from __future__ import annotations

import json
import unittest

from tools.metric_contract import BASELINE, capture, render


class TestMetricContract(unittest.TestCase):
    def test_metric_contract_is_current(self) -> None:
        expected = BASELINE.read_text(encoding="utf-8")
        self.assertEqual(render(capture()), expected)

    def test_contract_identifies_inputs_and_schema(self) -> None:
        payload = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "oaei-bioml-eval.metric-contract/1")
        self.assertEqual(payload["baseline_version"], "0.1.0.dev0")
        self.assertEqual(len(payload["inputs"]), 7)
        self.assertTrue(all(len(value) == 64 for value in payload["inputs"].values()))


if __name__ == "__main__":
    unittest.main()
