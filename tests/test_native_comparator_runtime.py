"""Release-runtime evidence tests kept independent of optional reasoner imports."""

from __future__ import annotations

import hashlib
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tools import native_compare


class TestNativeComparatorRuntime(unittest.TestCase):
    def test_pyelk_rust_contract_attests_extension_without_synthetic_flag(self) -> None:
        report = {
            "provenance": {
                "reasoner": {
                    "backend": {
                        "name": "rust",
                        "native_available": True,
                        "effective_workers": 12,
                    }
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            extension = Path(directory) / "_native.abi3.so"
            extension.write_bytes(b"installed pyELK extension")
            with mock.patch.object(
                native_compare.importlib.util,
                "find_spec",
                return_value=types.SimpleNamespace(origin=str(extension)),
            ):
                evidence = native_compare._runtime_evidence(
                    report,
                    reasoner="elk",
                    require_accelerated=True,
                )

        self.assertTrue(evidence["accelerated"])
        self.assertEqual(evidence["backend"]["name"], "rust")
        self.assertEqual(
            evidence["native_artifact"]["sha256"],
            hashlib.sha256(b"installed pyELK extension").hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
