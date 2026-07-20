"""Contract checks for the encoded-handoff phase benchmark."""

from __future__ import annotations

import importlib.util
import unittest
from unittest import mock

_HAS_STACK = all(importlib.util.find_spec(module) is not None for module in ("pyowl_core", "pyelk"))


@unittest.skipUnless(_HAS_STACK, "the optional shared OWL/reasoner stack is unavailable")
class TestO3NativeBenchmark(unittest.TestCase):
    def test_in_process_measurement_keeps_identity_and_does_not_encode_wire(self) -> None:
        from benchmarks import bench_o3_native

        with mock.patch.object(
            bench_o3_native.pyowl_core,
            "encode_snapshot",
            side_effect=AssertionError("in-process benchmark encoded a core wire artifact"),
        ):
            result = bench_o3_native.run(
                4,
                2,
                reasoner="elk",
                backend="python",
                timeout_s=None,
            )

        self.assertEqual(result["schema"], "oaei-bioml-eval.o3-native-benchmark/2")
        self.assertEqual(
            result["identity"],
            {
                "composite_retains_source": True,
                "composite_retains_target": True,
                "reasoner_retains_composite": True,
            },
        )
        handoff = result["compiler_handoff"]
        self.assertEqual(handoff["ingestion_path"], "scalar-python")
        self.assertGreater(handoff["consumer_compile_seconds"], 0.0)
        counters = handoff["counters"]
        self.assertGreater(counters["materialized_scalar_rows"], 0)
        evidence = result["materialization_and_copy"]
        self.assertEqual(evidence["benchmark_wire_encode_calls"], 0)
        self.assertFalse(evidence["complete_public_counter_coverage"])
        self.assertIn("wire_encoder_calls", evidence["missing_public_counters"])
        self.assertNotIn("wire_encode", result["seconds"])
        self.assertEqual(len(result["results"]["denominator_sha256"]), 64)
        self.assertEqual(len(result["results"]["unsatisfiable_sha256"]), 64)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
