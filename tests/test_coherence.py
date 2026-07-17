"""
Alignment coherence metric, shared-view orchestration, native reasoner factory,
and the HermiT-timeout -> ELK gate. The reported value is a degree of
INCOHERENCE: 0 = clean, higher = worse.
"""
from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from oaei_bioml_eval.coherence import cli as coherence_cli
from oaei_bioml_eval.coherence import metrics, structural
from oaei_bioml_eval.coherence.bridge import (
    compose_alignment_views,
    normalize_correspondences,
)
from oaei_bioml_eval.coherence.native_reasoners import HermiTTimeoutError
from oaei_bioml_eval.coherence.reasoner import (
    CoherenceReasoner,
    UnsatResult,
    load_reasoner,
)
from oaei_bioml_eval.coherence.report import (
    score_global_coherence,
    score_global_coherence_files,
    score_local_coherence,
    score_local_coherence_files,
    score_reference_coherence,
)
from oaei_bioml_eval.equivalence.metrics import _COUNT_METRICS as _EQUIV_COUNTS
from oaei_bioml_eval.io import write_tsv

try:
    import pyowl_core as _pyowl_core
except ImportError:
    _pyowl_core = None

A, B, C, D = (f"http://ex.org/{x}" for x in "ABCD")
# src: A,B,C with DisjointClasses(A,B); tgt: B,D. align A=B -> A,B unsatisfiable.
_SRC_OFN = (
    "Prefix(:=<http://ex.org/>)\nOntology(<http://ex.org/src>\n"
    f"  Declaration(Class(<{A}>))\n  Declaration(Class(<{B}>))\n  Declaration(Class(<{C}>))\n"
    f"  DisjointClasses(<{A}> <{B}>)\n)\n"
)
_TGT_OFN = (
    "Prefix(:=<http://ex.org/>)\nOntology(<http://ex.org/tgt>\n"
    f"  Declaration(Class(<{B}>))\n  Declaration(Class(<{D}>))\n)\n"
)


##
# Tier A — pure metric core, factory, bridge writer, stub
# -------------------------------------------------------
##

class TestMetricsCore(unittest.TestCase):
    def test_global_ratio_is_incoherence_degree(self):
        self.assertEqual(metrics.global_coherence_ratio(2, 4), 0.5)
        self.assertEqual(metrics.global_coherence_ratio(0, 4), 0.0)   # clean

    def test_global_ratio_div_zero_is_zero(self):
        self.assertEqual(metrics.global_coherence_ratio(0, 0), 0.0)
        self.assertEqual(metrics.global_coherence_ratio(3, 0), 0.0)   # empty signature -> 0, not error

    def test_global_ratio_quantized_12dp(self):
        self.assertEqual(metrics.global_coherence_ratio(2, 3), round(2 / 3, 12))

    def test_local_aggregate_mean_and_count(self):
        m = metrics.local_coherence_aggregate([True, False, True, False])
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.5, 4))

    def test_local_aggregate_empty_is_zero(self):
        m = metrics.local_coherence_aggregate([])
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.0, 0))

    def test_macro_sums_counts_means_rates_drops_annotations(self):
        macro = metrics.macro_average_across_tasks({
            "t1": {"global_coherence": 0.4, "unsatisfiable_count": 2, "union_class_count": 5,
                   "reasoner_used": "hermit", "lower_bound": False},
            "t2": {"global_coherence": 0.6, "unsatisfiable_count": 3, "union_class_count": 5,
                   "reasoner_used": "elk", "lower_bound": True},
        })
        self.assertAlmostEqual(macro["global_coherence"], 0.5)   # rate -> mean
        self.assertEqual(macro["unsatisfiable_count"], 5.0)      # count -> sum
        self.assertEqual(macro["union_class_count"], 10.0)
        self.assertNotIn("reasoner_used", macro)                 # annotations dropped
        self.assertNotIn("lower_bound", macro)

    def test_micro_recomputes_global_and_weights_local(self):
        per_task = {
            "small": {
                "global_coherence": 1.0,
                "unsatisfiable_count": 1,
                "union_class_count": 1,
                "local_coherence": 1.0,
                "local_coherence_queries": 1,
            },
            "large": {
                "global_coherence": 0.0,
                "unsatisfiable_count": 0,
                "union_class_count": 9,
                "local_coherence": 0.0,
                "local_coherence_queries": 9,
            },
        }
        macro = metrics.aggregate_across_tasks(per_task, average="macro")
        micro = metrics.aggregate_across_tasks(per_task, average="micro")
        self.assertEqual(macro["global_coherence"], 0.5)
        self.assertEqual(micro["global_coherence"], 0.1)
        self.assertEqual(micro["local_coherence"], 0.1)
        self.assertEqual(micro["union_class_count"], 10.0)

    def test_count_keys_disjoint_from_equivalence(self):
        # the two subtasks share a metrics.json; a colliding count key would mis-sum in macro
        self.assertTrue(metrics._COUNT_METRICS_COHERENCE.isdisjoint(_EQUIV_COUNTS))


class TestReasonerFactory(unittest.TestCase):
    def test_default_is_native(self):
        self.assertEqual(load_reasoner().name, "native")

    def test_obsolete_environment_variable_is_ignored(self):
        with mock.patch.dict("os.environ", {"OAEI_COHERENCE_BACKEND": "robot"}):
            self.assertEqual(load_reasoner().name, "native")

    def test_removed_backend_flag_has_migration_error(self):
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", stderr):
            coherence_cli.main(
                ["global", "a.tsv", "source.owl", "target.owl", "--backend=robot"]
            )
        self.assertIn("--backend was removed in 0.2.0", stderr.getvalue())

    def test_removed_runtime_flag_has_migration_error(self):
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", stderr):
            coherence_cli.main(
                ["global", "a.tsv", "source.owl", "target.owl", "--robot-jar", "r.jar"]
            )
        self.assertIn("--robot-jar was removed in 0.2.0", stderr.getvalue())


class TestBridgeWriter(unittest.TestCase):
    def test_normalization_covers_all_frozen_aliases(self):
        self.assertEqual(
            normalize_correspondences(
                [
                    (A, B),
                    (A, B, "equivalent"),
                    (A, B, "equiv"),
                    (B, C, "<"),
                    (B, C, "<="),
                    (C, D, ">"),
                    (C, D, ">="),
                    (A, A, "="),
                ]
            ),
            ((A, B, "="), (B, C, "<="), (C, D, ">=")),
        )

    def test_unsupported_relation_and_shape_fail_before_composition(self):
        with self.assertRaises(ValueError):
            normalize_correspondences([(A, B, "?")])
        with self.assertRaises(ValueError):
            normalize_correspondences([(A,)])

class TestStructuralStub(unittest.TestCase):
    def test_proxy_is_a_stub(self):
        with self.assertRaises(NotImplementedError):
            structural.structural_coherence_proxy("anything")


##
# Orchestrator + gate, driven by a shared-view stub
# -------------------------------------------------
##

class _SharedViewStub(CoherenceReasoner):
    """Record the exact composite on every native reasoner pass."""

    name = "stub"

    def __init__(self, denominator, unsat_by_reasoner, *, timeout_on=()):
        self._denominator = tuple(sorted(denominator))
        self._unsat = unsat_by_reasoner
        self._timeout_on = set(timeout_on)
        self.view_calls = []

    def unsatisfiable_classes_view(self, ontology, *, which, timeout_s):
        self.view_calls.append(ontology)
        if which in self._timeout_on:
            if which == "hermit":
                raise HermiTTimeoutError(f"stub timeout on {which}")
            raise TimeoutError(f"stub timeout on {which}")
        return UnsatResult(tuple(sorted(self._unsat.get(which, ()))), which, 0.0)


class _FakeComposite:
    def __init__(self, source, target, pairs):
        self.source = source
        self.target = target
        self.pairs = tuple(pairs)


def _fake_core(coerce):
    module = types.ModuleType("pyowl_core")
    for name in (
        "Class",
        "EntityKind",
        "EquivalentClasses",
        "IRI",
        "OntologyComposite",
        "OntologyDelta",
        "SubClassOf",
        "compose_views",
    ):
        setattr(module, name, object())
    module.coerce_snapshot = coerce
    return module


def _write_sub(tmp: Path, pairs) -> Path:
    path = tmp / "sub.tsv"
    write_tsv(path, [{"SrcEntity": s, "TgtEntity": t} for s, t in pairs], ["SrcEntity", "TgtEntity"])
    return path


def _write_ranked(tmp: Path, rows) -> Path:
    path = tmp / "ranked.tsv"
    write_tsv(path, [{"SrcEntity": s, "TgtCandidates": repr(list(pool))} for s, pool in rows],
              ["SrcEntity", "TgtCandidates"])
    return path


class TestSnapshotFirstAPI(unittest.TestCase):
    @contextmanager
    def _patch(self, stub):
        with mock.patch(
            "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.compose_alignment_views",
            side_effect=_FakeComposite,
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.named_class_iris",
            return_value=stub._denominator,
        ):
            yield

    def test_global_hands_exact_views_to_reasoner_once(self):
        source_view, target_view = object(), object()
        stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
        with self._patch(stub):
            metrics = score_global_coherence([(A, B)], source_view, target_view)
        self.assertIs(stub.view_calls[0].source, source_view)
        self.assertIs(stub.view_calls[0].target, target_view)
        self.assertEqual(len(stub.view_calls), 1)
        self.assertEqual(metrics["global_coherence"], 0.5)

    def test_reference_and_local_keep_existing_metric_semantics(self):
        source_view, target_view = object(), object()
        reference_stub = _SharedViewStub({A, B, C, D}, {"elk": {A}})
        with self._patch(reference_stub):
            reference = score_reference_coherence(
                [(A, B, "="), (A, B, "=")], source_view, target_view
            )
        self.assertEqual(reference["asserted_correspondences"], 1)
        self.assertEqual(reference["reasoner_used"], "elk")
        self.assertTrue(reference["lower_bound"])

        local_stub = _SharedViewStub({A, B, C, D}, {"hermit": {A}})
        with self._patch(local_stub):
            local = score_local_coherence(
                [(A, B), (A, B), (C, D)], source_view, target_view
            )
        self.assertEqual(local_stub.view_calls[0].pairs, ((A, B, "="), (C, D, "=")))
        self.assertEqual(local["local_coherence_queries"], 3)
        self.assertEqual(local["local_coherence"], round(2 / 3, 12))

    def test_timeout_fallback_receives_the_same_composite(self):
        stub = _SharedViewStub({A, B}, {"elk": {A}}, timeout_on={"hermit"})
        with self._patch(stub):
            metrics = score_global_coherence([(A, B)], object(), object())
        self.assertEqual(metrics["reasoner_used"], "elk")
        self.assertEqual(len(stub.view_calls), 2)
        self.assertIs(stub.view_calls[0], stub.view_calls[1])

    def test_wrapper_coerces_each_provider_once_and_forwards_core_options(self):
        source_view, target_view = object(), object()

        class Provider:
            def __init__(self, view):
                self.view = view
                self.calls = 0

            def owl_snapshot(self):
                self.calls += 1
                return self.view

        source, target = Provider(source_view), Provider(target_view)
        coercions = []

        def coerce(value, **kwargs):
            coercions.append((value, kwargs))
            return value.owl_snapshot()

        fake_core = _fake_core(coerce)
        stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
        options, resolver, token = object(), object(), object()
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub), mock.patch.dict(
            sys.modules, {"pyowl_core": fake_core}
        ):
            submission = _write_sub(Path(tmp), [(A, B)])
            metrics = score_global_coherence_files(
                submission,
                source,
                target,
                source_document_iri="urn:document:source",
                target_document_iri="urn:document:target",
                load_options=options,
                resolver=resolver,
                cancellation_token=token,
            )
        self.assertEqual((source.calls, target.calls), (1, 1))
        self.assertEqual([item[0] for item in coercions], [source, target])
        self.assertEqual(
            [item[1]["document_iri"] for item in coercions],
            ["urn:document:source", "urn:document:target"],
        )
        self.assertTrue(all(item[1]["options"] is options for item in coercions))
        self.assertTrue(all(item[1]["resolver"] is resolver for item in coercions))
        self.assertTrue(all(item[1]["cancellation_token"] is token for item in coercions))
        self.assertIs(stub.view_calls[0].source, source_view)
        self.assertIs(stub.view_calls[0].target, target_view)
        self.assertEqual(metrics["global_coherence"], 0.5)

    def test_core_loader_exception_is_not_wrapped_or_retried(self):
        expected = RuntimeError("import closure unavailable")
        calls = 0

        def coerce(value, **kwargs):
            nonlocal calls
            del value, kwargs
            calls += 1
            raise expected

        fake_core = _fake_core(coerce)
        stub = _SharedViewStub({A, B}, {"hermit": set()})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub), mock.patch.dict(
            sys.modules, {"pyowl_core": fake_core}
        ):
            submission = _write_sub(Path(tmp), [(A, B)])
            with self.assertRaises(RuntimeError) as raised:
                score_global_coherence_files(submission, "source", "target")
        self.assertIs(raised.exception, expected)
        self.assertEqual(calls, 1)
        self.assertEqual(stub.view_calls, [])


if _pyowl_core is not None:
    class TestConcreteCoreIdentity(unittest.TestCase):
        def test_bridge_delta_has_exact_core_axioms_without_serialization(self):
            source = _pyowl_core.coerce_snapshot(
                _SRC_OFN.encode("utf-8"), document_iri="urn:document:source"
            )
            target = _pyowl_core.coerce_snapshot(
                _TGT_OFN.encode("utf-8"), document_iri="urn:document:target"
            )
            source_axioms = tuple(source.iter_axioms())
            target_axioms = tuple(target.iter_axioms())
            correspondences = [
                (A, B, "equivalent"),
                (A, B, "equiv"),
                (C, D, "<"),
                (B, D, ">"),
                (A, A, "="),
            ]
            with mock.patch.object(
                Path, "write_text", side_effect=AssertionError("serialization attempted")
            ):
                merged = compose_alignment_views(source, target, correspondences)

            def cls(iri):
                return _pyowl_core.Class(_pyowl_core.IRI(iri))
            expected = frozenset(
                {
                    _pyowl_core.EquivalentClasses(frozenset((cls(A), cls(B)))),
                    _pyowl_core.SubClassOf(cls(C), cls(D)),
                    _pyowl_core.SubClassOf(cls(D), cls(B)),
                }
            )
            self.assertEqual(frozenset(merged.delta.add_axioms), expected)
            self.assertIs(merged.members[0].view, source)
            self.assertIs(merged.members[1].view, target)
            self.assertTrue(
                all(
                    before is after
                    for before, after in zip(
                        source_axioms, source.iter_axioms(), strict=True
                    )
                )
            )
            self.assertTrue(
                all(
                    before is after
                    for before, after in zip(
                        target_axioms, target.iter_axioms(), strict=True
                    )
                )
            )

        def test_concrete_snapshots_reach_shared_seam_by_identity(self):
            source = _pyowl_core.coerce_snapshot(
                _SRC_OFN.encode("utf-8"), document_iri="urn:document:source"
            )
            target = _pyowl_core.coerce_snapshot(
                _TGT_OFN.encode("utf-8"), document_iri="urn:document:target"
            )
            stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
            with mock.patch(
                "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
            ), mock.patch.object(
                _pyowl_core.OntologyComposite,
                "materialize",
                side_effect=AssertionError("materialization attempted"),
            ):
                score_global_coherence([(A, B)], source, target)
            merged = stub.view_calls[0]
            self.assertIsInstance(merged, _pyowl_core.OntologyComposite)
            self.assertIs(merged.members[0].view, source)
            self.assertIs(merged.members[1].view, target)
            self.assertEqual(tuple(merged.member_roles.values()), ("source", "target"))
            self.assertIs(_pyowl_core.coerce_snapshot(merged), merged)
            self.assertEqual(len(stub.view_calls), 1)

        def test_overlay_is_retained_and_its_base_is_unchanged(self):
            source = _pyowl_core.coerce_snapshot(
                _SRC_OFN.encode("utf-8"), document_iri="urn:document:source"
            )
            target = _pyowl_core.coerce_snapshot(
                _TGT_OFN.encode("utf-8"), document_iri="urn:document:target"
            )
            base_axioms = tuple(source.iter_axioms())
            addition = _pyowl_core.Declaration(
                _pyowl_core.Class(_pyowl_core.IRI("http://ex.org/OverlayOnly"))
            )
            overlay = _pyowl_core.apply_delta(
                source, _pyowl_core.OntologyDelta(add_axioms={addition})
            )
            stub = _SharedViewStub(
                {A, B, C, D, "http://ex.org/OverlayOnly"}, {"hermit": {A, B}}
            )
            with mock.patch(
                "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
            ):
                score_global_coherence([(A, B)], overlay, target)
            merged = stub.view_calls[0]
            self.assertIs(merged.members[0].view, overlay)
            self.assertIs(overlay.base, source)
            self.assertEqual(tuple(source.iter_axioms()), base_axioms)
            self.assertNotIn(addition, base_axioms)
            self.assertIn(addition, tuple(overlay.iter_axioms()))

        def test_stream_wrapper_coerces_once_and_keeps_caller_streams_open(self):
            source_stream = io.BytesIO(_SRC_OFN.encode("utf-8"))
            target_stream = io.BytesIO(_TGT_OFN.encode("utf-8"))
            original = _pyowl_core.coerce_snapshot
            calls = []

            def counted(source, **kwargs):
                calls.append(source)
                return original(source, **kwargs)

            stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
            with tempfile.TemporaryDirectory() as tmp, mock.patch(
                "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
            ), mock.patch.object(_pyowl_core, "coerce_snapshot", side_effect=counted):
                submission = _write_sub(Path(tmp), [(A, B)])
                score_global_coherence_files(
                    submission,
                    source_stream,
                    target_stream,
                    source_document_iri="urn:document:source",
                    target_document_iri="urn:document:target",
                )
            self.assertEqual(calls, [source_stream, target_stream])
            self.assertFalse(source_stream.closed)
            self.assertFalse(target_stream.closed)
            merged = stub.view_calls[0]
            self.assertIsInstance(merged.members[0].view, _pyowl_core.OntologySnapshot)
            self.assertIsInstance(merged.members[1].view, _pyowl_core.OntologySnapshot)
            self.assertEqual(len(stub.view_calls), 1)


class TestOrchestratorGate(unittest.TestCase):
    @contextmanager
    def _patch(self, stub):
        fake_core = _fake_core(lambda value, **kwargs: value)
        with mock.patch(
            "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.compose_alignment_views",
            side_effect=_FakeComposite,
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.named_class_iris",
            return_value=stub._denominator,
        ), mock.patch.dict(sys.modules, {"pyowl_core": fake_core}):
            yield

    def test_global_counts_and_degree(self):
        stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            m = score_global_coherence_files(sub, "src", "tgt", reasoner="hermit")
        self.assertEqual((m["unsatisfiable_count"], m["union_class_count"]), (2, 4))
        self.assertEqual(m["global_coherence"], 0.5)
        self.assertEqual((m["reasoner_used"], m["lower_bound"]), ("hermit", False))

    def test_hermit_timeout_falls_back_to_elk_lower_bound(self):
        stub = _SharedViewStub(
            {A, B, C, D}, {"elk": {A}}, timeout_on={"hermit"}
        )
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            m = score_global_coherence_files(sub, "src", "tgt", reasoner="hermit", timeout_s=1.0)
        self.assertEqual((m["reasoner_used"], m["lower_bound"]), ("elk", True))
        self.assertEqual(m["unsatisfiable_count"], 1)   # ELK's (lower) count

    def test_numerator_must_subset_denominator(self):
        stub = _SharedViewStub(
            {A, B}, {"hermit": {"http://ex.org/Z"}}
        )  # Z absent from the signature
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            with self.assertRaises(AssertionError):
                score_global_coherence_files(sub, "src", "tgt")

    def test_local_blame_and_no_answer_dropped(self):
        # committed {(A,B),(C,D)}; unsat {A,B}: q1 blamed, q2 clean, q3 no-answer dropped
        stub = _SharedViewStub({A, B, C, D}, {"hermit": {A, B}})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            ranked = _write_ranked(Path(tmp), [(A, [B, C]), (C, [D]), (A, [])])
            m = score_local_coherence_files(ranked, "src", "tgt", reasoner="hermit")
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.5, 2))
        self.assertEqual(
            stub.view_calls[0].pairs, ((A, B, "="), (C, D, "="))
        )  # canonical deduped bridge

    def test_local_no_commitments_is_zero(self):
        stub = _SharedViewStub({A, B}, {"hermit": set()})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            ranked = _write_ranked(Path(tmp), [(A, []), (C, [])])
            m = score_local_coherence_files(ranked, "src", "tgt")
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.0, 0))

class TestMalformedIris(unittest.TestCase):
    """a SNOMED RF2->OWL artifact: an entity 'IRI' that is several IRIs run together by newlines"""
    _RUNON = "http://snomed.info/id/1295447006\nhttp://snomed.info/id/1295449009"

    def test_validator_flags_embedded_whitespace(self):
        from oaei_bioml_eval.coherence.bridge import invalid_alignment_iris
        self.assertEqual(invalid_alignment_iris([(A, B)]), [])
        self.assertEqual(invalid_alignment_iris([(A, self._RUNON)]), [self._RUNON])

    def test_global_raises_before_the_reasoner(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub.tsv"
            write_tsv(sub, [{"SrcEntity": A, "TgtEntity": self._RUNON}], ["SrcEntity", "TgtEntity"])
            with self.assertRaises(ValueError) as cm:
                score_global_coherence_files(sub, "/no/src.owl", "/no/tgt.owl")
        self.assertIn("whitespace", str(cm.exception))

    def test_skip_invalid_drops_the_bad_pair(self):
        stub = _SharedViewStub({A, B}, {"hermit": set()})
        fake_core = _fake_core(lambda value, **kwargs: value)
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.compose_alignment_views",
            side_effect=_FakeComposite,
        ), mock.patch(
            "oaei_bioml_eval.coherence.report.named_class_iris",
            return_value=(A, B),
        ), mock.patch.dict(sys.modules, {"pyowl_core": fake_core}):
            sub = Path(tmp) / "sub.tsv"
            write_tsv(sub, [{"SrcEntity": A, "TgtEntity": B},
                            {"SrcEntity": A, "TgtEntity": self._RUNON}], ["SrcEntity", "TgtEntity"])
            score_global_coherence_files(sub, "/no/src.owl", "/no/tgt.owl", skip_invalid=True)
        self.assertEqual(
            stub.view_calls[0].pairs, ((A, B, "="),)
        )  # the run-on pair dropped, the clean one kept


if __name__ == "__main__":
    unittest.main()
