"""
Alignment coherence: the pure metric core (Tier A), the report
orchestrator + the HermiT-timeout -> ELK gate driven by a STUB reasoner (Tier B,
no JVM/ROBOT), and the real reasoner path on a 3-class clash fixture (Tier C —
skip-guarded on ROBOT-on-PATH / DeepOnto). The degree is a degree of INCOHERENCE:
0 = clean, higher = worse.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oaei_bioml_eval.coherence import metrics, reasoner as reasoner_mod, structural
from oaei_bioml_eval.coherence.reasoner import (
    CoherenceReasoner,
    MergedOntology,
    UnsatResult,
    _parse_unsatisfiable,
    load_reasoner,
    write_bridge_ofn,
)
from oaei_bioml_eval.coherence.report import (
    score_global_coherence_files,
    score_local_coherence_files,
)
from oaei_bioml_eval.equivalence.metrics import _COUNT_METRICS as _EQUIV_COUNTS
from oaei_bioml_eval.io import write_tsv

_HAS_ROBOT = shutil.which("robot") is not None
try:
    import deeponto  # noqa: F401
    _HAS_DEEPONTO = True
except Exception:
    _HAS_DEEPONTO = False

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


def _fixture(tmp: Path) -> tuple[Path, Path]:
    (tmp / "src.ofn").write_text(_SRC_OFN, encoding="utf-8")
    (tmp / "tgt.ofn").write_text(_TGT_OFN, encoding="utf-8")
    return tmp / "src.ofn", tmp / "tgt.ofn"


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

    def test_count_keys_disjoint_from_equivalence(self):
        # the two subtasks share a metrics.json; a colliding count key would mis-sum in macro
        self.assertTrue(metrics._COUNT_METRICS_COHERENCE.isdisjoint(_EQUIV_COUNTS))


class TestReasonerFactory(unittest.TestCase):
    def test_default_is_robot(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OAEI_COHERENCE_BACKEND", None)
            self.assertEqual(load_reasoner().name, "robot")

    def test_env_selects_backend(self):
        with mock.patch.dict("os.environ", {"OAEI_COHERENCE_BACKEND": "robot"}):
            self.assertEqual(load_reasoner().name, "robot")

    def test_owlapi_is_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            load_reasoner("owlapi")

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            load_reasoner("nope")


class TestBridgeWriter(unittest.TestCase):
    def test_bridge_is_sorted_deduped_and_declares(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.ofn"
            n = write_bridge_ofn(path, [(B, A), (A, A), (B, A)])   # self-pair dropped, dup collapsed
            text = path.read_text()
        self.assertEqual(n, 1)                                     # one EquivalentClasses axiom
        self.assertIn(f"EquivalentClasses(<{B}> <{A}>)", text)
        self.assertIn(f"Declaration(Class(<{A}>))", text)
        self.assertNotIn(f"EquivalentClasses(<{A}> <{A}>)", text)  # no self-equivalence


class TestStructuralStub(unittest.TestCase):
    def test_proxy_is_a_stub(self):
        with self.assertRaises(NotImplementedError):
            structural.structural_coherence_proxy("anything")


class TestUnsatLogParse(unittest.TestCase):
    """the ROBOT-log parse (numerator) — the unsat set comes from the log, NOT the dump module"""

    def test_listed_iris_minus_nothing(self):
        log = ("ReasonerHelper - There are 2 unsatisfiable classes in the ontology.\n"
               f"ReasonerHelper -     unsatisfiable: {A}\n"
               f"ReasonerHelper -     unsatisfiable: {B}\n")
        self.assertEqual(_parse_unsatisfiable(log), {A, B})

    def test_truncated_log_raises(self):           # reported 5, listed 1 -> fail loud, never under-report
        with self.assertRaises(RuntimeError):
            _parse_unsatisfiable(f"There are 5 unsatisfiable classes in the ontology.\n    unsatisfiable: {A}\n")

    def test_nonzero_without_report_raises(self):  # a real ROBOT failure, not an incoherence report
        with self.assertRaises(RuntimeError):
            _parse_unsatisfiable("java.lang.OutOfMemoryError: Java heap space\n")


##
# Tier B — orchestrator + gate, driven by a STUB reasoner (no JVM/ROBOT)
# ----------------------------------------------------------------------
##

class _StubReasoner(CoherenceReasoner):
    """canned signature + unsat sets; can simulate a HermiT wall-clock timeout"""
    name = "stub"

    def __init__(self, denominator, unsat_by_reasoner, *, timeout_on=()):
        self._denominator = tuple(sorted(denominator))
        self._unsat = unsat_by_reasoner
        self._timeout_on = set(timeout_on)
        self.merged_pairs = None

    def merge(self, src_owl, tgt_owl, pairs):
        self.merged_pairs = sorted(set(pairs))
        return MergedOntology(self.merged_pairs)

    def named_classes(self, merged):
        return self._denominator

    def unsatisfiable_classes(self, merged, *, which, timeout_s):
        if which in self._timeout_on:
            raise TimeoutError(f"stub timeout on {which}")
        return UnsatResult(tuple(sorted(self._unsat.get(which, ()))), which, 0.0)


def _write_sub(tmp: Path, pairs) -> Path:
    path = tmp / "sub.tsv"
    write_tsv(path, [{"SrcEntity": s, "TgtEntity": t} for s, t in pairs], ["SrcEntity", "TgtEntity"])
    return path


def _write_ranked(tmp: Path, rows) -> Path:
    path = tmp / "ranked.tsv"
    write_tsv(path, [{"SrcEntity": s, "TgtCandidates": repr(list(pool))} for s, pool in rows],
              ["SrcEntity", "TgtCandidates"])
    return path


class TestOrchestratorGate(unittest.TestCase):
    def _patch(self, stub):
        return mock.patch("oaei_bioml_eval.coherence.report.load_reasoner", return_value=stub)

    def test_global_counts_and_degree(self):
        stub = _StubReasoner({A, B, C, D}, {"hermit": {A, B}})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            m = score_global_coherence_files(sub, "src", "tgt", reasoner="hermit")
        self.assertEqual((m["unsatisfiable_count"], m["union_class_count"]), (2, 4))
        self.assertEqual(m["global_coherence"], 0.5)
        self.assertEqual((m["reasoner_used"], m["lower_bound"]), ("hermit", False))

    def test_hermit_timeout_falls_back_to_elk_lower_bound(self):
        stub = _StubReasoner({A, B, C, D}, {"elk": {A}}, timeout_on={"hermit"})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            m = score_global_coherence_files(sub, "src", "tgt", reasoner="hermit", timeout_s=1.0)
        self.assertEqual((m["reasoner_used"], m["lower_bound"]), ("elk", True))
        self.assertEqual(m["unsatisfiable_count"], 1)   # ELK's (lower) count

    def test_numerator_must_subset_denominator(self):
        stub = _StubReasoner({A, B}, {"hermit": {"http://ex.org/Z"}})   # Z absent from the signature
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            sub = _write_sub(Path(tmp), [(A, B)])
            with self.assertRaises(AssertionError):
                score_global_coherence_files(sub, "src", "tgt")

    def test_local_blame_and_no_answer_dropped(self):
        # committed {(A,B),(C,D)}; unsat {A,B}: q1 blamed, q2 clean, q3 no-answer dropped
        stub = _StubReasoner({A, B, C, D}, {"hermit": {A, B}})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            ranked = _write_ranked(Path(tmp), [(A, [B, C]), (C, [D]), (A, [])])
            m = score_local_coherence_files(ranked, "src", "tgt", reasoner="hermit")
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.5, 2))
        self.assertEqual(stub.merged_pairs, [(A, B), (C, D)])   # deduped committed bridge

    def test_local_no_commitments_is_zero(self):
        stub = _StubReasoner({A, B}, {"hermit": set()})
        with tempfile.TemporaryDirectory() as tmp, self._patch(stub):
            ranked = _write_ranked(Path(tmp), [(A, []), (C, [])])
            m = score_local_coherence_files(ranked, "src", "tgt")
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.0, 0))


##
# Tier C — the real reasoner path on the 3-class clash fixture
# -----------------------------------------------------------
##

@unittest.skipUnless(_HAS_ROBOT, "ROBOT not on PATH")
class TestRobotFixture(unittest.TestCase):
    def test_unsat_is_exactly_a_b_denominator_four(self):
        from oaei_bioml_eval.coherence.reasoner import RobotReasoner
        with tempfile.TemporaryDirectory() as tmp:
            src, tgt = _fixture(Path(tmp))
            r = RobotReasoner()
            merged = r.merge(src, tgt, [(A, B)])
            try:
                self.assertEqual(set(r.named_classes(merged)), {A, B, C, D})           # signature
                hermit = r.unsatisfiable_classes(merged, which="hermit", timeout_s=600)
                elk = r.unsatisfiable_classes(merged, which="elk", timeout_s=600)
            finally:
                r.dispose(merged)
        self.assertEqual(set(hermit.unsatisfiable), {A, B})                              # owl:Nothing excluded
        self.assertTrue(set(elk.unsatisfiable).issubset(set(hermit.unsatisfiable)))      # EL <= DL lower bound

    def test_global_clash_and_coherent(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, tgt = _fixture(Path(tmp))
            clash = score_global_coherence_files(_write_sub(Path(tmp), [(A, B)]), src, tgt, timeout_s=600)
            ok = score_global_coherence_files(_write_sub(Path(tmp), [(A, D)]), src, tgt, timeout_s=600)
        self.assertEqual((clash["unsatisfiable_count"], clash["union_class_count"]), (2, 4))
        self.assertEqual(clash["global_coherence"], 0.5)
        self.assertEqual(ok["unsatisfiable_count"], 0)            # A=D, no disjointness -> clean
        self.assertEqual(ok["global_coherence"], 0.0)

    def test_local_half_incoherent(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, tgt = _fixture(Path(tmp))
            ranked = _write_ranked(Path(tmp), [(A, [B, C]), (C, [D]), (A, [])])
            m = score_local_coherence_files(ranked, src, tgt, timeout_s=600)
        self.assertEqual((m["local_coherence"], m["local_coherence_queries"]), (0.5, 2))

    def test_numerator_excludes_explanatory_context(self):
        # regression for the dump-over-count bug: only A is unsatisfiable, but its
        # explanation spans the satisfiable P,Q,R,T. the log-parse must return {A}; the
        # old --dump-unsatisfiable path returned all five (the explanatory module).
        from oaei_bioml_eval.coherence.reasoner import RobotReasoner
        onto = (
            "Prefix(:=<http://ex.org/>)\nOntology(<http://ex.org/o>\n"
            f"  Declaration(Class(<{A}>))\n  Declaration(Class(<http://ex.org/P>))\n"
            "  Declaration(Class(<http://ex.org/Q>))\n  Declaration(Class(<http://ex.org/R>))\n"
            "  Declaration(Class(<http://ex.org/T>))\n"
            f"  SubClassOf(<{A}> <http://ex.org/P>)\n  SubClassOf(<{A}> <http://ex.org/Q>)\n"
            "  SubClassOf(<http://ex.org/P> <http://ex.org/R>)\n  SubClassOf(<http://ex.org/R> <http://ex.org/T>)\n"
            "  DisjointClasses(<http://ex.org/P> <http://ex.org/Q>)\n)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "o.ofn").write_text(onto, encoding="utf-8")
            (Path(tmp) / "empty.ofn").write_text("Ontology(<http://ex.org/empty>)\n", encoding="utf-8")
            r = RobotReasoner()
            merged = r.merge(Path(tmp) / "o.ofn", Path(tmp) / "empty.ofn", [])   # clash already in o.ofn
            try:
                denom = set(r.named_classes(merged))
                unsat = r.unsatisfiable_classes(merged, which="hermit", timeout_s=600).unsatisfiable
            finally:
                r.dispose(merged)
        self.assertEqual(set(unsat), {A})                              # ONLY A, not the 5-class explanation
        self.assertTrue({"http://ex.org/P", "http://ex.org/T"} <= denom)   # context IS in the denominator


@unittest.skipUnless(_HAS_DEEPONTO, "DeepOnto not installed")
class TestDeepOntoFixture(unittest.TestCase):
    def test_in_process_matches_robot(self):
        from oaei_bioml_eval.coherence.reasoner import DeepOntoReasoner
        with tempfile.TemporaryDirectory() as tmp:
            src, tgt = _fixture(Path(tmp))
            r = DeepOntoReasoner(heap="2g")
            merged = r.merge(src, tgt, [(A, B)])
            try:
                self.assertEqual(set(r.named_classes(merged)), {A, B, C, D})
                hermit = r.unsatisfiable_classes(merged, which="hermit", timeout_s=None)
            finally:
                r.dispose(merged)
        self.assertEqual(set(hermit.unsatisfiable), {A, B})


class TestMalformedIris(unittest.TestCase):
    """a SNOMED RF2->OWL artifact: an entity 'IRI' that is several IRIs run together by newlines"""
    _RUNON = "http://snomed.info/id/1295447006\nhttp://snomed.info/id/1295449009"

    def test_validator_flags_embedded_whitespace(self):
        from oaei_bioml_eval.coherence.reasoner import invalid_alignment_iris
        self.assertEqual(invalid_alignment_iris([(A, B)]), [])
        self.assertEqual(invalid_alignment_iris([(A, self._RUNON)]), [self._RUNON])

    def test_global_raises_before_the_reasoner(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub.tsv"
            write_tsv(sub, [{"SrcEntity": A, "TgtEntity": self._RUNON}], ["SrcEntity", "TgtEntity"])
            with self.assertRaises(ValueError) as cm:   # clear message, not a cryptic ROBOT abort
                score_global_coherence_files(sub, "/no/src.owl", "/no/tgt.owl")
        self.assertIn("whitespace", str(cm.exception))

    def test_skip_invalid_drops_the_bad_pair(self):
        captured = {}

        class _Stub(CoherenceReasoner):
            def merge(self, s, t, pairs):
                captured["pairs"] = sorted(set(pairs))
                return MergedOntology(None)

            def named_classes(self, merged):
                return (A, B)

            def unsatisfiable_classes(self, merged, *, which, timeout_s):
                return UnsatResult((), which, 0.0)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("oaei_bioml_eval.coherence.report.load_reasoner", return_value=_Stub()):
            sub = Path(tmp) / "sub.tsv"
            write_tsv(sub, [{"SrcEntity": A, "TgtEntity": B},
                            {"SrcEntity": A, "TgtEntity": self._RUNON}], ["SrcEntity", "TgtEntity"])
            score_global_coherence_files(sub, "/no/src.owl", "/no/tgt.owl", skip_invalid=True)
        self.assertEqual(captured["pairs"], [(A, B)])   # the run-on pair dropped, the clean one kept


if __name__ == "__main__":
    unittest.main()
