"""Reasoner adapter seam plus quarantined pre-native differential backends.

The official O2 path composes exact shared views in ``coherence.bridge`` and
passes one ``OntologyComposite`` to ``unsatisfiable_classes_view``. It never asks
a reasoner to merge or serialize ontologies. The file-handle methods and Java
classes below remain temporarily for O0 differential evidence and are deleted in
O4 after native adapters land in O3. ``load_reasoner`` currently picks:

  * 'robot'     the repository-only ROBOT differential classifier;
  * 'deeponto'  an OPTIONAL in-process fast-path reusing a warm JVM via direct
                OWLAPI; behind the [deeponto] extra (lazy import). Older HermiT/ELK
                than ROBOT -> exploratory, never mixed with ROBOT numbers on a board.
  * 'owlapi'    a direct-OWLAPI/JPype binding (we would own the JAR lifecycle); not
                implemented (use 'robot' or 'deeponto').

All bindings drive the SAME HermiT (DL, exact) / ELK (EL, a `>=` lower bound)
underneath, so they are interchangeable up to reasoner VERSION — counts are
version-dependent, so pin one for released numbers. The HermiT-timeout -> ELK 
gate lives ABOVE this seam (report.py), so the policy is backend-independent. 
The shared composite is built once and reused for its signature and every native
reasoner pass, so the denominator and axioms are identical.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from abc import ABC
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bridge import normalize_correspondences

OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"
_TRIVIAL = frozenset({OWL_THING, OWL_NOTHING})   # excluded everywhere (numerator AND denominator)


@dataclass(frozen=True)
class UnsatResult:
    """the unsatisfiable named classes (sorted, owl:Nothing excluded) + provenance"""
    unsatisfiable: tuple[str, ...]
    reasoner_used: str          # 'hermit' | 'elk'
    elapsed_seconds: float


@dataclass
class MergedOntology:
    """
    Legacy differential-oracle handle. Official shared-view scoring never creates
    this wrapper; it passes the exact core composite to a native adapter.
    """
    handle: Any
    workdir: Path | None = None


class CoherenceReasoner(ABC):
    name: str = "base"
    # Native adapters opt into the O2 shared composite handoff. Legacy Java
    # differential oracles keep the file-only methods until O4 deletes them.
    accepts_ontology_views: bool = False

    def merge(
        self, src_owl: Path, tgt_owl: Path, pairs: Iterable[object]
    ) -> MergedOntology:
        """Legacy file-only merge retained solely for differential capture."""
        del src_owl, tgt_owl, pairs
        raise TypeError(
            f"{type(self).__name__} does not implement the legacy file oracle"
        )

    def named_classes(self, merged: MergedOntology) -> tuple[str, ...]:
        """Legacy file-oracle denominator."""
        del merged
        raise TypeError(f"{type(self).__name__} does not implement the legacy file oracle")

    def unsatisfiable_classes(self, merged: MergedOntology, *, which: str, timeout_s: float | None) -> UnsatResult:
        """Legacy file-oracle classification."""
        del merged, which, timeout_s
        raise TypeError(f"{type(self).__name__} does not implement the legacy file oracle")

    def unsatisfiable_classes_view(
        self,
        ontology: object,
        *,
        which: str,
        timeout_s: float | None,
    ) -> UnsatResult:
        """Classify the exact core composite without materializing or reparsing it."""
        del ontology, which, timeout_s
        raise TypeError(
            f"{type(self).__name__} is a legacy file-only differential oracle; "
            "shared coherence requires a native view adapter"
        )

    def dispose(self, merged: MergedOntology) -> None:
        """release the merged handle (ROBOT tempdir / DeepOnto ontology); default no-op"""


def load_reasoner(backend: str | None = None, *, robot_jar=None, java=None,
                  heap: str | None = None, robot_cmd=None) -> CoherenceReasoner:
    """
    pick a backend: explicit arg > $OAEI_COHERENCE_BACKEND > 'robot'. robot_jar/java/
    robot_cmd are ROBOT-only (not forwarded to DeepOnto); heap applies to either.
    """
    backend = (backend or os.environ.get("OAEI_COHERENCE_BACKEND") or "robot").lower()
    if backend == "robot":
        return RobotReasoner(robot_jar=robot_jar, java=java, heap=heap, robot_cmd=robot_cmd)
    if backend == "deeponto":
        return DeepOntoReasoner(heap=heap or "16g")
    if backend == "owlapi":
        raise NotImplementedError("the direct-OWLAPI/JPype backend is not implemented; use 'robot' (default) or 'deeponto'.")
    raise ValueError(f"unknown coherence backend {backend!r}; known: robot, deeponto, owlapi.")


##
# bridge + signature helpers (shared)
# -----------------------------------
##

# the denominator: named classes of the MERGED ontology, via `robot query` (a Jena SPARQL
# over the loaded model) — NOT rdflib, whose recursive Turtle parser blows the recursion
# limit on SNOMED's deeply-nested class expressions, and NOT RDF/XML, which won't serialise
# SNOMED's digit-IRI annotation properties. owl:Thing/Nothing are subtracted by the caller.
_CLASS_SIGNATURE_SPARQL = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "SELECT ?c WHERE { ?c a owl:Class . FILTER(isIRI(?c)) }\n"
)


def read_iri_column(path: Path) -> set[str]:
    """the single-column IRI results of a ROBOT SPARQL SELECT — skip the header, strip the <>"""
    iris: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        next(handle, None)   # the `?c` header row
        for line in handle:
            iri = line.strip()
            if iri.startswith("<") and iri.endswith(">"):
                iri = iri[1:-1]
            if iri:
                iris.add(iri)
    return iris


_UNSAT_IRI = re.compile(r"unsatisfiable:\s*(\S+)")          # ROBOT logs one line per unsatisfiable class
_UNSAT_TOTAL = re.compile(r"There are (\d+) unsatisfiable")


def _parse_unsatisfiable(log: str) -> set[str]:
    """
    the unsatisfiable named-class IRIs from `robot reason`'s log, minus owl:Nothing.
    fail LOUD rather than report a wrong count: a non-zero exit with no unsatisfiability
    report is a real ROBOT failure (OOM / bad input / a fully inconsistent ontology), and
    a "There are N" that disagrees with the listed IRIs means the log was truncated (use
    the deeponto backend at that scale).
    """
    listed = {m.group(1) for m in _UNSAT_IRI.finditer(log)}
    total = _UNSAT_TOTAL.search(log)
    if total is None and not listed:
        raise RuntimeError(f"ROBOT reason exited non-zero with no unsatisfiable-class report:\n{log[-2000:]}")
    if total is not None and int(total.group(1)) != len(listed):
        raise RuntimeError(f"ROBOT reason reported {total.group(1)} unsatisfiable classes but logged "
                           f"{len(listed)} — the log was truncated; use the deeponto backend at this scale.")
    return listed - _TRIVIAL


##
# ROBOT (default backend) — subprocess
# ------------------------------------
##

_TERMINATE_GRACE_SECONDS = 10.0

class RobotReasoner(CoherenceReasoner):
    """
    Quarantined ROBOT classifier used by the repository-only differential oracle.

    The tool-owned oracle supplies the legacy serialized merge. ROBOT then logs the
    exact unsatisfiable classes ("unsatisfiable: <IRI>") and exits non-zero on
    incoherence; we parse that log and cross-check its "There are N" total. This is
    intentionally not `--dump-unsatisfiable`: that explanatory module also carries
    satisfiable context classes and would over-count the numerator.
    """
    name = "robot"

    def __init__(self, *, robot_jar: str | Path | None = None, java: str | None = None,
                 heap: str | None = None, robot_cmd: list[str] | None = None) -> None:
        jar = robot_jar or os.environ.get("ROBOT_JAR")
        self._robot_jar = Path(jar) if jar else None
        self._java = java or os.environ.get("JAVA") or "java"
        self._heap = heap   # e.g. "16g" -> -Xmx16g for SNOMED scale
        self._explicit = list(robot_cmd) if robot_cmd else None

    def _base_cmd(self) -> list[str]:
        if self._explicit:
            return list(self._explicit)
        on_path = shutil.which("robot")
        if on_path:
            return [on_path]
        if self._robot_jar and self._robot_jar.exists():
            heap = ["-Xmx" + self._heap] if self._heap else []
            return [self._java, *heap, "-jar", str(self._robot_jar)]
        raise FileNotFoundError("ROBOT not found: put `robot` on PATH, set $ROBOT_JAR, or pass robot_jar=.")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._heap:  # the `robot` wrapper reads this
            env["ROBOT_JAVA_ARGS"] = f"-Xmx{self._heap}"
        return env

    def named_classes(self, merged: MergedOntology) -> tuple[str, ...]:
        query = merged.workdir / "classes.rq"
        query.write_text(_CLASS_SIGNATURE_SPARQL, encoding="utf-8")
        out = merged.workdir / "classes.tsv"
        code, log = self._run([*self._base_cmd(), "query", "--input", str(merged.handle),
                               "--query", str(query), str(out)], timeout_s=None, label="query:classes")
        if code != 0:
            raise RuntimeError(f"ROBOT query (class signature) failed (exit {code}):\n{log[-2000:]}")
        return tuple(sorted(read_iri_column(out) - _TRIVIAL))

    def unsatisfiable_classes(self, merged: MergedOntology, *, which: str, timeout_s: float | None) -> UnsatResult:
        if which not in ("hermit", "elk"):
            raise ValueError(f"reasoner must be 'hermit' or 'elk', got {which!r}.")
        start = time.perf_counter()
        code, log = self._run([*self._base_cmd(), "reason", "--reasoner", which, "--input", str(merged.handle)],
                              timeout_s=timeout_s, label=f"reason:{which}")
        elapsed = time.perf_counter() - start
        unsat = () if code == 0 else tuple(sorted(_parse_unsatisfiable(log)))   # exit 0 == coherent
        return UnsatResult(unsat, which, elapsed)

    def dispose(self, merged: MergedOntology) -> None:
        if merged.workdir:
            shutil.rmtree(merged.workdir, ignore_errors=True)

    def _run(self, cmd: list[str], *, timeout_s: float | None, label: str) -> tuple[int, str]:
        """run ROBOT in its own session -> (returncode, combined stdout+stderr); killpg + TimeoutError on the gate"""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, env=self._env())
        try:
            out, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            raise TimeoutError(f"ROBOT {label} exceeded {timeout_s}s") from None
        return proc.returncode, (out or b"").decode("utf-8", "replace")


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


##
# DeepOnto (optional in-process fast-path) — direct OWLAPI on a warm JVM
# ----------------------------------------------------------------------
##

class DeepOntoReasoner(CoherenceReasoner):
    """
    optional warm-JVM fast-path: load both OWLs in-process, add the target axioms +
    the bridge to one ontology, classify via OWLAPI directly (DeepOnto does not wrap
    getUnsatisfiableClasses). Lazy `deeponto` import. Exploratory only — its bundled
    HermiT/ELK are OLDER than ROBOT's, so never mix its counts with ROBOT's on a
    leaderboard.
    """
    name = "deeponto"

    def __init__(self, *, heap: str = "16g") -> None:
        self._heap = heap

    def _ensure_jvm(self) -> None:
        # lazy; starts JPype + the OWLAPI classpath
        from deeponto import init_jvm               # type: ignore
        init_jvm(self._heap)                        # deeponto guards against a double start

    def merge(self, src_owl: Path, tgt_owl: Path, pairs: Iterable) -> MergedOntology:
        self._ensure_jvm()
        from jpype import JArray, JClass                                # type: ignore
        from java.io import File                                        # type: ignore
        from org.semanticweb.owlapi.apibinding import OWLManager        # type: ignore
        from org.semanticweb.owlapi.model import IRI                    # type: ignore
        expr_type = JClass("org.semanticweb.owlapi.model.OWLClassExpression")  # disambiguate the varargs overload
        manager = OWLManager.createOWLOntologyManager()
        factory = manager.getOWLDataFactory()
        merged = manager.loadOntologyFromOntologyDocument(File(str(src_owl)))
        target = manager.loadOntologyFromOntologyDocument(File(str(tgt_owl)))
        manager.addAxioms(merged, target.getAxioms())
        for src, tgt, relation in normalize_correspondences(pairs):
            src_cls, tgt_cls = factory.getOWLClass(IRI.create(src)), factory.getOWLClass(IRI.create(tgt))
            if relation == "=":   # EquivalentClasses(src, tgt)
                operands = JArray(expr_type)([src_cls, tgt_cls])
                manager.addAxiom(merged, factory.getOWLEquivalentClassesAxiom(operands))
            elif relation == "<=":   # SubClassOf(src, tgt)
                manager.addAxiom(merged, factory.getOWLSubClassOfAxiom(src_cls, tgt_cls))
            else:   # ">=" -> SubClassOf(tgt, src)
                manager.addAxiom(merged, factory.getOWLSubClassOfAxiom(tgt_cls, src_cls))
        manager.removeOntology(target)
        return MergedOntology((manager, merged))

    def named_classes(self, merged: MergedOntology) -> tuple[str, ...]:
        from org.semanticweb.owlapi.model.parameters import Imports    # type: ignore
        _manager, onto = merged.handle
        named = {str(c.getIRI()) for c in onto.getClassesInSignature(Imports.EXCLUDED) if not c.isAnonymous()}
        return tuple(sorted(named - _TRIVIAL))

    def unsatisfiable_classes(self, merged: MergedOntology, *, which: str, timeout_s: float | None) -> UnsatResult:
        # timeout_s is advisory here: in-process OWLAPI reasoning is not interruptible
        # without killing the JVM — the wall-clock gate is the ROBOT subprocess's job.
        _manager, onto = merged.handle
        factory = _owlapi_reasoner_factory(which)
        start = time.perf_counter()
        reasoner = factory.createReasoner(onto)
        try:
            # getUnsatisfiableClasses() returns a Node (all unsat classes are mutually
            # equivalent ~ owl:Nothing) -> getEntities(), NOT a NodeSet's getFlattened()
            node = reasoner.getUnsatisfiableClasses()
            unsat = {str(c.getIRI()) for c in node.getEntities() if not c.isAnonymous()} - _TRIVIAL
        finally:
            reasoner.dispose()
        return UnsatResult(tuple(sorted(unsat)), which, time.perf_counter() - start)

    def dispose(self, merged: MergedOntology) -> None:
        manager, onto = merged.handle
        try:
            manager.removeOntology(onto)
        except Exception:
            pass


def _owlapi_reasoner_factory(which: str):
    if which == "hermit":
        from org.semanticweb.HermiT import ReasonerFactory             # type: ignore
        return ReasonerFactory()
    if which == "elk":
        from org.semanticweb.elk.owlapi import ElkReasonerFactory      # type: ignore
        return ElkReasonerFactory()
    raise ValueError(f"reasoner must be 'hermit' or 'elk', got {which!r}.")
