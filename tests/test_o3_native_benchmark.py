"""Contract checks for the encoded-handoff phase benchmark."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_HAS_STACK = all(importlib.util.find_spec(module) is not None for module in ("pyowl_core", "pyelk"))


def _sample(
    benchmark,
    *,
    ingestion_path: str,
    wall_seconds: float,
    rss_bytes: int,
    counter_overrides: dict[str, int] | None = None,
) -> dict[str, object]:
    counters = {
        "base_flattening_bytes": 0,
        "encoded_private_ir_bytes": 0,
        "encoded_staging_copy_bytes": 4,
        "materialized_scalar_rows": 0,
        "parser_calls": 0,
        "per_row_ffi_calls": 0,
        "resolver_calls": 0,
        "wire_decoder_calls": 0,
        "wire_encoder_calls": 0,
    }
    if counter_overrides:
        counters.update(counter_overrides)
    handoff = {"ingestion_path": ingestion_path, "counters": counters}
    seconds = {phase: 0.0 for phase in benchmark._PIPELINE_WALL_PHASES}
    seconds["reasoning_total"] = wall_seconds
    return {
        "schema": benchmark.SINGLE_RUN_SCHEMA,
        "python": "3.12.3",
        "core": "0.1.0.dev0",
        "reasoner": "elk",
        "backend": {"name": "python" if ingestion_path.startswith("scalar") else "rust"},
        "class_count_per_side": 4,
        "bridge_count": 2,
        "denominator_count": 8,
        "unsatisfiable_count": 4,
        "inconsistent": False,
        "inputs": {
            "source_sha256": "a" * 64,
            "target_sha256": "b" * 64,
            "bridge_sha256": "c" * 64,
        },
        "compiler_handoff": handoff,
        "materialization_and_copy": benchmark._counter_evidence(handoff),
        "results": {
            "denominator_sha256": "d" * 64,
            "unsatisfiable_sha256": "e" * 64,
            "composite_structural_fingerprint": "f" * 64,
            "provenance_sha256": "0" * 64,
        },
        "seconds": seconds,
        "process_peak_rss_bytes": rss_bytes,
    }


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
        self.assertFalse(evidence["zero_forbidden_public_counters"])
        self.assertIn("wire_encoder_calls", evidence["missing_public_counters"])
        self.assertNotIn("wire_encode", result["seconds"])
        self.assertEqual(len(result["results"]["denominator_sha256"]), 64)
        self.assertEqual(len(result["results"]["unsatisfiable_sha256"]), 64)

    def test_acceptance_protocol_alternates_isolated_cohorts_and_applies_medians(self) -> None:
        from benchmarks import bench_o3_native

        calls: list[str] = []

        def sample_runner(request):
            calls.append(request.backend)
            if request.backend == "python":
                return _sample(
                    bench_o3_native,
                    ingestion_path="scalar-python",
                    wall_seconds=10.0,
                    rss_bytes=100,
                )
            return _sample(
                bench_o3_native,
                ingestion_path="encoded-native",
                wall_seconds=12.0,
                rss_bytes=119,
            )

        report = bench_o3_native.run_acceptance_protocol(
            4,
            2,
            reasoner="elk",
            baseline_backend="python",
            candidate_backend="rust",
            timeout_s=None,
            warmups=1,
            repetitions=3,
            sample_runner=sample_runner,
        )

        self.assertEqual(
            calls,
            ["python", "rust", "rust", "python", "python", "rust", "rust", "python"],
        )
        self.assertEqual(report["schema"], bench_o3_native.ACCEPTANCE_SCHEMA)
        self.assertEqual(report["baseline"]["median_pipeline_wall_seconds"], 10.0)
        self.assertEqual(report["candidate"]["median_pipeline_wall_seconds"], 12.0)
        self.assertAlmostEqual(
            report["evaluation"]["wall"]["candidate_to_baseline_ratio"],
            1.2,
        )
        self.assertAlmostEqual(
            report["evaluation"]["rss"]["candidate_to_baseline_ratio"],
            1.19,
        )
        self.assertTrue(report["evaluation"]["protocol_passed"])
        self.assertFalse(report["evaluation"]["release_accepted"])
        self.assertTrue(report["workload"]["diagnostic_only"])

    def test_acceptance_rejects_semantic_and_regression_mismatches(self) -> None:
        from benchmarks import bench_o3_native

        baseline = [
            _sample(
                bench_o3_native,
                ingestion_path="scalar-python",
                wall_seconds=10.0,
                rss_bytes=100,
            )
            for _ in range(3)
        ]
        candidate = [
            _sample(
                bench_o3_native,
                ingestion_path="encoded-native",
                wall_seconds=12.51,
                rss_bytes=121,
            )
            for _ in range(3)
        ]
        candidate[1] = copy.deepcopy(candidate[1])
        candidate[1]["results"]["unsatisfiable_sha256"] = "9" * 64

        report = bench_o3_native.evaluate_acceptance_samples(
            baseline,
            candidate,
            warmups=1,
            repetitions=3,
            baseline_backend="python",
            candidate_backend="rust",
        )

        failed = report["evaluation"]["failed_checks"]
        self.assertIn("fixed_input_and_semantic_identity", failed)
        self.assertIn("wall_regression_within_25_percent", failed)
        self.assertIn("rss_regression_within_20_percent", failed)
        self.assertFalse(report["identity"]["fixed"])

    def test_acceptance_rejects_scalar_candidate_and_nonzero_forbidden_counter(self) -> None:
        from benchmarks import bench_o3_native

        baseline = [
            _sample(
                bench_o3_native,
                ingestion_path="scalar-python",
                wall_seconds=10.0,
                rss_bytes=100,
            )
            for _ in range(3)
        ]
        candidate = [
            _sample(
                bench_o3_native,
                ingestion_path="scalar-native",
                wall_seconds=9.0,
                rss_bytes=90,
                counter_overrides={"parser_calls": 1},
            )
            for _ in range(3)
        ]

        report = bench_o3_native.evaluate_acceptance_samples(
            baseline,
            candidate,
            warmups=1,
            repetitions=3,
            baseline_backend="python",
            candidate_backend="rust",
        )

        failed = report["evaluation"]["failed_checks"]
        self.assertIn("candidate_selected_encoded_native", failed)
        self.assertIn("candidate_complete_public_counter_coverage", failed)
        self.assertIn("candidate_zero_forbidden_public_counters", failed)

    def test_acceptance_rejects_nonzero_counter_even_with_complete_encoded_coverage(self) -> None:
        from benchmarks import bench_o3_native

        baseline = [
            _sample(
                bench_o3_native,
                ingestion_path="scalar-python",
                wall_seconds=10.0,
                rss_bytes=100,
            )
            for _ in range(3)
        ]
        candidate = [
            _sample(
                bench_o3_native,
                ingestion_path="encoded-native",
                wall_seconds=9.0,
                rss_bytes=90,
                counter_overrides={"parser_calls": 1},
            )
            for _ in range(3)
        ]

        report = bench_o3_native.evaluate_acceptance_samples(
            baseline,
            candidate,
            warmups=1,
            repetitions=3,
            baseline_backend="python",
            candidate_backend="rust",
        )
        checks = report["evaluation"]["checks"]
        self.assertTrue(checks["candidate_selected_encoded_native"])
        self.assertTrue(checks["candidate_complete_public_counter_coverage"])
        self.assertFalse(checks["candidate_zero_forbidden_public_counters"])

    def test_enforce_mode_returns_nonzero_and_names_failed_checks(self) -> None:
        from benchmarks import bench_o3_native

        report = {
            "evaluation": {
                "protocol_passed": False,
                "failed_checks": ["candidate_selected_encoded_native"],
            }
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(bench_o3_native, "run_acceptance_protocol", return_value=report),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = bench_o3_native.main(
                [
                    "--reasoner",
                    "elk",
                    "--backend",
                    "rust",
                    "--baseline-backend",
                    "python",
                    "--enforce",
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertIn("candidate_selected_encoded_native", stderr.getvalue())

    def test_tiny_sampling_is_explicitly_non_accepting(self) -> None:
        from benchmarks import bench_o3_native

        baseline = [
            _sample(
                bench_o3_native,
                ingestion_path="scalar-python",
                wall_seconds=10.0,
                rss_bytes=100,
            )
        ]
        candidate = [
            _sample(
                bench_o3_native,
                ingestion_path="encoded-native",
                wall_seconds=9.0,
                rss_bytes=90,
            )
        ]
        report = bench_o3_native.evaluate_acceptance_samples(
            baseline,
            candidate,
            warmups=0,
            repetitions=1,
            baseline_backend="python",
            candidate_backend="rust",
        )

        self.assertFalse(report["evaluation"]["checks"]["sampling_sufficient"])
        self.assertFalse(report["evaluation"]["protocol_passed"])
        self.assertFalse(report["workload"]["release_gate_eligible"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
