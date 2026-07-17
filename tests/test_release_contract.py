"""Coordinated 0.2 version, metadata, documentation, and SBOM guardrails."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, cast

import oaei_bioml_eval

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _metadata_value(name: str) -> str:
    match = re.search(rf'(?m)^{re.escape(name)} = "([^"]+)"$', PYPROJECT)
    if match is None:
        raise AssertionError(f"missing metadata value: {name}")
    return match.group(1)


class TestReleaseContract(unittest.TestCase):
    def test_version_and_python_floor_move_together(self) -> None:
        self.assertEqual(_metadata_value("version"), "0.2.0")
        self.assertEqual(oaei_bioml_eval.__version__, "0.2.0")
        self.assertEqual(_metadata_value("requires-python"), ">=3.10")
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            self.assertIn(f"Programming Language :: Python :: {version}", PYPROJECT)

    def test_base_is_empty_and_extras_are_bounded(self) -> None:
        project = PYPROJECT.split("[project.optional-dependencies]", 1)[0]
        self.assertRegex(project, r"(?m)^dependencies = \[\]$")
        reasoner = PYPROJECT.split("reasoner = [", 1)[1].split("]", 1)[0]
        for requirement in (
            "pyowl-core>=0.1,<0.2",
            "pyHermiT>=0.1,<0.2",
            "pyelk-reasoner>=0.1,<0.2",
        ):
            self.assertIn(requirement, reasoner)
        self.assertIn('rdf = ["rdflib>=7.0,<8"]', PYPROJECT)
        self.assertNotIn("deeponto", PYPROJECT.lower())

    def test_typed_marker_and_release_documents_exist(self) -> None:
        for path in (
            ROOT / "src" / "oaei_bioml_eval" / "py.typed",
            ROOT / "CHANGELOG.md",
            ROOT / "docs" / "coherence.md",
            ROOT / "docs" / "installation.md",
            ROOT / "docs" / "migration-0.2.md",
            ROOT / "SBOM.spdx.json",
        ):
            self.assertTrue(path.is_file(), path)

    def test_sbom_matches_declared_release(self) -> None:
        payload = cast(
            dict[str, Any],
            json.loads((ROOT / "SBOM.spdx.json").read_text(encoding="utf-8")),
        )
        self.assertEqual(payload["spdxVersion"], "SPDX-2.3")
        packages = {
            package["name"]: package
            for package in cast(list[dict[str, Any]], payload["packages"])
        }
        self.assertEqual(packages["oaei-bioml-eval"]["versionInfo"], "0.2.0")
        self.assertEqual(
            set(packages),
            {
                "oaei-bioml-eval",
                "pyowl-core",
                "pyHermiT",
                "pyelk-reasoner",
                "rdflib",
            },
        )
        self.assertEqual(packages["pyHermiT"]["licenseDeclared"], "LGPL-3.0-or-later")

    def test_ci_covers_supported_python_matrix(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            self.assertIn(f'"{version}"', workflow)
        self.assertNotIn("setup-java", workflow)


if __name__ == "__main__":
    unittest.main()
