#!/usr/bin/env python3
"""Fail-closed audit for the portable OAEI Bio-ML release pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = (".class", ".dll", ".dylib", ".jar", ".pyd", ".so")


def _project_metadata(root: Path) -> tuple[str, str, str]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    return str(project["name"]), str(project["version"]), str(project["requires-python"])


def _safe_names(names: list[str]) -> list[str]:
    safe: list[str] = []
    folded: set[str] = set()
    for name in names:
        normalized = name.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not normalized
            or normalized.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError(f"unsafe archive member: {name!r}")
        key = normalized.casefold()
        if key in folded:
            raise ValueError(f"duplicate archive member: {name!r}")
        folded.add(key)
        safe.append(normalized)
    return safe


def _wheel_report(path: Path, *, name: str, version: str, python: str) -> dict[str, object]:
    expected = f"oaei_bioml_eval-{version}-py3-none-any.whl"
    errors: list[str] = []
    if path.name != expected:
        errors.append(f"wheel filename {path.name!r} != {expected!r}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = _safe_names(archive.namelist())
            payloads = {member: archive.read(member) for member in names}
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        return {"artifact": path.name, "kind": "wheel", "errors": [str(error)], "passed": False}
    metadata_path = f"oaei_bioml_eval-{version}.dist-info/METADATA"
    wheel_path = f"oaei_bioml_eval-{version}.dist-info/WHEEL"
    if metadata_path not in payloads:
        errors.append(f"missing {metadata_path}")
    else:
        metadata = BytesParser(policy=default).parsebytes(payloads[metadata_path])
        expected_metadata = {
            "Name": name,
            "Version": version,
            "Requires-Python": python,
        }
        for field, value in expected_metadata.items():
            if metadata.get(field) != value:
                errors.append(f"{field} {metadata.get(field)!r} != {value!r}")
    if wheel_path not in payloads:
        errors.append(f"missing {wheel_path}")
    else:
        wheel = BytesParser(policy=default).parsebytes(payloads[wheel_path])
        if wheel.get("Root-Is-Purelib") != "true":
            errors.append("wheel is not marked Root-Is-Purelib: true")
        if wheel.get_all("Tag", []) != ["py3-none-any"]:
            errors.append(f"wheel tags are not exactly py3-none-any: {wheel.get_all('Tag', [])!r}")
    if "oaei_bioml_eval/py.typed" not in payloads:
        errors.append("wheel does not contain oaei_bioml_eval/py.typed")
    errors.extend(
        f"forbidden binary payload: {member}"
        for member in names
        if member.lower().endswith(FORBIDDEN_SUFFIXES)
    )
    payload = path.read_bytes()
    return {
        "artifact": path.name,
        "kind": "wheel",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "members": len(names),
        "errors": errors,
        "passed": not errors,
    }


def _sdist_report(
    path: Path,
    *,
    name: str,
    version: str,
    python: str,
) -> dict[str, object]:
    expected = f"oaei_bioml_eval-{version}.tar.gz"
    errors: list[str] = []
    if path.name != expected:
        errors.append(f"sdist filename {path.name!r} != {expected!r}")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            names = _safe_names([member.name.rstrip("/") for member in members])
            if any(not (member.isfile() or member.isdir()) for member in members):
                errors.append("sdist contains a non-regular member")
            payloads = {
                member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
                for member in members
                if member.isfile()
            }
    except (OSError, ValueError, tarfile.TarError) as error:
        return {"artifact": path.name, "kind": "sdist", "errors": [str(error)], "passed": False}
    root = f"oaei_bioml_eval-{version}/"
    required = (
        "CHANGELOG.md",
        "LICENSE",
        "README.md",
        "SBOM.spdx.json",
        "pyproject.toml",
        "release/build-requirements.txt",
        "release/core-compatibility.json",
        "release/owner-release-override.md",
        "src/oaei_bioml_eval/__init__.py",
        "src/oaei_bioml_eval/py.typed",
    )
    for relative in required:
        if f"{root}{relative}" not in names:
            errors.append(f"sdist missing {relative}")
    metadata_path = f"{root}PKG-INFO"
    if metadata_path not in payloads:
        errors.append("sdist missing PKG-INFO")
    else:
        metadata = BytesParser(policy=default).parsebytes(payloads[metadata_path])
        expected_metadata = {
            "Name": name,
            "Version": version,
            "Requires-Python": python,
        }
        for field, value in expected_metadata.items():
            if metadata.get(field) != value:
                errors.append(f"sdist {field} {metadata.get(field)!r} != {value!r}")
    errors.extend(
        f"forbidden binary payload: {member}"
        for member in names
        if member.lower().endswith(FORBIDDEN_SUFFIXES)
    )
    payload = path.read_bytes()
    return {
        "artifact": path.name,
        "kind": "sdist",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "members": len(names),
        "errors": errors,
        "passed": not errors,
    }


def audit(directory: Path, *, root: Path = ROOT) -> dict[str, object]:
    name, version, python = _project_metadata(root)
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    expected_names = {
        f"oaei_bioml_eval-{version}-py3-none-any.whl",
        f"oaei_bioml_eval-{version}.tar.gz",
    }
    reports: list[dict[str, object]] = []
    errors: list[str] = []
    observed = {path.name for path in paths}
    if observed != expected_names:
        errors.append(
            f"distribution set {sorted(observed)!r} != authoritative set {sorted(expected_names)!r}"
        )
    for path in paths:
        report = (
            _wheel_report(path, name=name, version=version, python=python)
            if path.suffix == ".whl"
            else _sdist_report(path, name=name, version=version, python=python)
        )
        reports.append(report)
        errors.extend(f"{path.name}: {item}" for item in report["errors"])
    return {
        "schema": "oaei-bioml-eval.release-audit/1",
        "version": version,
        "artifacts": reports,
        "errors": errors,
        "passed": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = audit(args.directory.resolve(), root=args.root.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
