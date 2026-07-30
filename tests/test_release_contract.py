"""Coordinated 0.2 version, metadata, documentation, and SBOM guardrails."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import oaei_bioml_eval
from tools.audit_release import _project_metadata, audit

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
            ROOT / "release" / "owner-release-override.md",
        ):
            self.assertTrue(path.is_file(), path)

    def test_release_owner_override_is_explicit_and_does_not_rewrite_evidence(
        self,
    ) -> None:
        authorization = (ROOT / "release" / "owner-release-override.md").read_text(encoding="utf-8")
        self.assertIn("authorizes production publication", authorization)
        self.assertIn("accountable owner waiver", authorization)
        self.assertIn("does not rewrite the historical measurements", authorization)
        self.assertIn("remain mandatory operational checks", authorization)
        self.assertIn("trusted publication", authorization)
        self.assertIn("No credential or token is stored", authorization)

    def test_sbom_matches_declared_release(self) -> None:
        payload = cast(
            dict[str, Any],
            json.loads((ROOT / "SBOM.spdx.json").read_text(encoding="utf-8")),
        )
        self.assertEqual(payload["spdxVersion"], "SPDX-2.3")
        packages = {
            package["name"]: package for package in cast(list[dict[str, Any]], payload["packages"])
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
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            self.assertIn(f'"{version}"', workflow)
        self.assertNotIn("setup-java", workflow)

    def test_workflow_actions_are_pinned_to_commits(self) -> None:
        action_reference = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
        for workflow_path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line_number, line in enumerate(
                workflow_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                match = re.match(r"^\s*-\s+uses:\s+(\S+)", line)
                if match is None:
                    continue
                reference = match.group(1)
                self.assertRegex(
                    reference,
                    action_reference,
                    f"{workflow_path.name}:{line_number}: {reference}",
                )

    def test_atomic_release_is_tag_scoped_and_distribution_only(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn('tags: ["v*"]', workflow)
        self.assertIn("cmp dist-a/*.whl dist-b/*.whl", workflow)
        self.assertIn("cmp dist-a/*.tar.gz dist-b/*.tar.gz", workflow)
        self.assertIn("python tools/audit_release.py", workflow)
        self.assertIn("pyowl-core==0.1.1", workflow)
        self.assertIn("pyHermiT==0.1.2", workflow)
        self.assertIn("pyelk-reasoner==0.1.1", workflow)
        self.assertIn("--owner-matrix --require-encoded", workflow)
        self.assertIn("actions/attest-build-provenance@", workflow)
        self.assertIn("environment: pypi", workflow)
        publish = workflow.split("  publish:", maxsplit=1)[1]
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", publish)
        self.assertIn("permissions:\n      id-token: write", publish)
        self.assertNotIn("api-token", publish)
        self.assertIn("skip-existing: false", publish)
        self.assertNotIn("skip-existing: true", workflow)

    def test_release_auditor_rejects_an_incomplete_distribution_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = audit(Path(directory), root=ROOT)
        self.assertFalse(report["passed"])
        self.assertIn("authoritative set", cast(list[str], report["errors"])[0])

    def test_release_auditor_metadata_is_dependency_free_on_python_3_10(self) -> None:
        real_import = __import__

        def import_without_toml(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> Any:
            if name in {"tomli", "tomllib"}:
                raise ImportError(f"{name} is unavailable")
            return real_import(name, globals, locals, fromlist, level)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("builtins.__import__", side_effect=import_without_toml),
        ):
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "example"\nversion = "1.2.3"\n'
                'requires-python = ">=3.10"\n\n[tool.example]\nenabled = true\n',
                encoding="utf-8",
            )
            self.assertEqual(
                _project_metadata(root),
                ("example", "1.2.3", ">=3.10"),
            )


if __name__ == "__main__":
    unittest.main()
