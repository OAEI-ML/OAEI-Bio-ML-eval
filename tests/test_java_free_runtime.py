"""Static O4 guardrails for the installable Java-free runtime."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "oaei_bioml_eval"
FORBIDDEN_IMPORT_ROOTS = frozenset({"deeponto", "java", "jpype", "org", "subprocess"})
FORBIDDEN_CORE_PREFIXES = (
    "pyowl_core._native",
    "pyowl_core.backends",
    "pyowl_core.document.native_storage",
)


def _import_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return tuple(f"{node.module}.{alias.name}" for alias in node.names)
    return ()


class TestJavaFreeRuntime(unittest.TestCase):
    def test_installable_modules_have_no_java_runtime_imports(self) -> None:
        violations: list[str] = []
        for path in sorted(SOURCE.rglob("*.py")):
            relative = path.relative_to(SOURCE)
            if any(not part.isidentifier() for part in (*relative.parts[:-1], path.stem)):
                # Hatch excludes non-module conflict copies (for example
                # ``reasoner 2.py``).  They are local files, not installable
                # package modules, and must not weaken the scan of real code.
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                roots = {name.split(".", 1)[0] for name in _import_names(node)}
                forbidden = roots & FORBIDDEN_IMPORT_ROOTS
                if forbidden:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {sorted(forbidden)}"
                    )
        self.assertEqual(violations, [])

    def test_installable_modules_use_only_the_public_core_boundary(self) -> None:
        violations: list[str] = []
        for path in sorted(SOURCE.rglob("*.py")):
            relative = path.relative_to(SOURCE)
            if any(not part.isidentifier() for part in (*relative.parts[:-1], path.stem)):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for imported in _import_names(node):
                    if any(
                        imported == prefix or imported.startswith(f"{prefix}.")
                        for prefix in FORBIDDEN_CORE_PREFIXES
                    ):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {imported}")
        self.assertEqual(violations, [])

    def test_non_module_conflict_copies_are_excluded_from_artifacts(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('exclude = ["**/* 2.*"]', metadata)
        self.assertIn('exclude = "(^|/).* 2\\\\.[^/]+$"', metadata)

    def test_legacy_reasoner_types_are_not_installable(self) -> None:
        reasoner = (SOURCE / "coherence" / "reasoner.py").read_text(encoding="utf-8")
        for symbol in ("MergedOntology", "RobotReasoner", "DeepOntoReasoner"):
            self.assertNotIn(symbol, reasoner)

    def test_dependency_metadata_has_no_legacy_extra(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
        self.assertNotIn("deeponto", metadata)
        self.assertNotIn("robot_jar", metadata)

    def test_ordinary_coherence_tests_have_no_jvm_skip_gate(self) -> None:
        tests = (ROOT / "tests" / "test_coherence.py").read_text(encoding="utf-8")
        self.assertNotIn("skipUnless", tests)


if __name__ == "__main__":
    unittest.main()
