"""Static O4 guardrails for the installable Java-free runtime."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "oaei_bioml_eval"
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {"deeponto", "java", "jpype", "org", "subprocess"}
)


class TestJavaFreeRuntime(unittest.TestCase):
    def test_installable_modules_have_no_java_runtime_imports(self) -> None:
        violations: list[str] = []
        for path in sorted(SOURCE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots = {node.module.split(".", 1)[0]}
                else:
                    continue
                forbidden = roots & FORBIDDEN_IMPORT_ROOTS
                if forbidden:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {sorted(forbidden)}"
                    )
        self.assertEqual(violations, [])

    def test_legacy_reasoner_types_are_not_installable(self) -> None:
        reasoner = (SOURCE / "coherence" / "reasoner.py").read_text(
            encoding="utf-8"
        )
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
