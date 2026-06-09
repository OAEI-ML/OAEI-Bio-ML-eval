"""
oaei_bioml_eval.coherence.reasoner: the swappable reasoner backend.

The official coherence needs an OWL reasoner the light participant scorer cannot
carry, so it sits behind a tiny CoherenceReasoner seam — merge two OWLs + a bridge
of EquivalentClasses axioms, count the merged named-class signature, list the
unsatisfiable classes. `load_reasoner` picks the binding:

  * 'robot'     (DEFAULT) the OBO-standard CLI as a subprocess. NO JVM/torch in
                eval's deps; ROBOT is an external tool located on PATH / $ROBOT_JAR.
                Parsing its OWL output uses rdflib (the [reasoner] extra).
  * 'deeponto'  an OPTIONAL in-process fast-path reusing a warm JVM via direct
                OWLAPI; behind the [deeponto] extra (lazy import). Older HermiT/ELK
                than ROBOT -> exploratory, never mixed with ROBOT numbers on a board.
  * 'owlapi'    a direct-OWLAPI/JPype binding (we would own the JAR lifecycle); not
                implemented (use 'robot' or 'deeponto').

All bindings drive the SAME HermiT (DL, exact) / ELK (EL, a `>=` lower bound)
underneath, so they are interchangeable up to reasoner VERSION — counts are
version-dependent, so pin one for released numbers. The HermiT-timeout -> ELK 
gate lives ABOVE this seam (report.py), so the policy is backend-independent. 
The merged ontology is built ONCE and reasoned over by both HermiT and ELK, 
so the denominator + axioms are identical (`=`/`>=` comparable).
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    a backend-private handle to the merged ontology, reused for the signature + BOTH
    reasoner passes (built ONCE -> the denominator + axioms are identical across HermiT
    and ELK). `handle` is a Path (ROBOT's merged.owl) or an (manager, ontology) pair
    (DeepOnto); `workdir` is ROBOT's scratch, freed by dispose().
    """
    handle: Any
    workdir: Path | None = None


class CoherenceReasoner(ABC):
    name: str = "base"

    @abstractmethod
    def merge(self, src_owl: Path, tgt_owl: Path, pairs: Iterable[tuple[str, str]]) -> MergedOntology:
        """both OWLs + a bridge of one EquivalentClasses(src, tgt) per pair (named, IRIs sorted)"""

    @abstractmethod
    def named_classes(self, merged: MergedOntology) -> tuple[str, ...]:
        """merged class signature (Imports.EXCLUDED) minus owl:Thing/Nothing/anonymous, sorted"""

    @abstractmethod
    def unsatisfiable_classes(self, merged: MergedOntology, *, which: str, timeout_s: float | None) -> UnsatResult:
        """classify with 'hermit'|'elk'; sorted unsat named classes minus owl:Nothing. raises TimeoutError on the wall-clock gate"""

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

def write_bridge_ofn(path: Path, pairs: Iterable[tuple[str, str]]) -> int:
    """
    one EquivalentClasses(src, tgt) axiom per `=` correspondence, in OWL functional
    syntax (.ofn — ROBOT reads it directly). NAMED classes only; IRIs sorted for a
    canonical axiom set; each class declared so a stray IRI never reads as undeclared.
    """
    ordered = sorted({(str(s), str(t)) for s, t in pairs if str(s) != str(t)})
    iris = sorted({iri for pair in ordered for iri in pair})
    lines = ["Ontology(<https://w3id.org/oaei-bioml/coherence/bridge>"]
    lines += [f"  Declaration(Class(<{iri}>))" for iri in iris]
    lines += [f"  EquivalentClasses(<{s}> <{t}>)" for s, t in ordered]
    lines.append(")")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(ordered)


def named_classes_in_owl(path: Path) -> set[str]:
    """
    the named-class IRIs of an OWL file via rdflib (the [reasoner] extra) — class
    declarations plus IRIs in class position (covers used-but-undeclared classes),
    minus owl:Thing/Nothing and anonymous (blank-node) expressions.
    """
    from rdflib import Graph, URIRef                # type: ignore
    from rdflib.namespace import OWL, RDF, RDFS     # type: ignore

    graph = Graph()
    graph.parse(str(path))
    nodes = set(graph.subjects(RDF.type, OWL.Class))
    for predicate in (RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith):
        for subject, _p, obj in graph.triples((None, predicate, None)):
            nodes.add(subject)
            nodes.add(obj)
    return {str(n) for n in nodes if isinstance(n, URIRef)} - _TRIVIAL


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
    the default. `robot merge` (both OWLs + the bridge) then `robot reason --reasoner
    hermit|elk`: ROBOT logs the EXACT unsatisfiable classes ("unsatisfiable: <IRI>") and
    exits non-zero on incoherence; we parse that log (and cross-check its "There are N").
    NOT `--dump-unsatisfiable`: that dump is an explanatory/justification module — it
    carries the satisfiable context classes that explain the clash, so counting it would
    over-count the numerator.
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

    def merge(self, src_owl: Path, tgt_owl: Path, pairs: Iterable[tuple[str, str]]) -> MergedOntology:
        import tempfile
        workdir = Path(tempfile.mkdtemp(prefix="coh-robot-"))
        bridge = workdir / "bridge.ofn"
        write_bridge_ofn(bridge, pairs)
        merged = workdir / "merged.owl"
        code, log = self._run([*self._base_cmd(), "merge",
                               "--input", str(src_owl), "--input", str(tgt_owl), "--input", str(bridge),
                               "--output", str(merged)], timeout_s=None, label="merge")
        if code != 0:
            raise RuntimeError(f"ROBOT merge failed (exit {code}):\n{log[-2000:]}")
        return MergedOntology(merged, workdir)

    def named_classes(self, merged: MergedOntology) -> tuple[str, ...]:
        return tuple(sorted(named_classes_in_owl(merged.handle)))

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

    def merge(self, src_owl: Path, tgt_owl: Path, pairs: Iterable[tuple[str, str]]) -> MergedOntology:
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
        for src, tgt in sorted({(str(s), str(t)) for s, t in pairs if str(s) != str(t)}):
            operands = JArray(expr_type)([factory.getOWLClass(IRI.create(src)), factory.getOWLClass(IRI.create(tgt))])
            manager.addAxiom(merged, factory.getOWLEquivalentClassesAxiom(operands))
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
