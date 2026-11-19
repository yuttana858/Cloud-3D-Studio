"""Proof security contract and public-only engine integration."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cadconvert.operator import safe_result
from tools.cadconvert.prepare import prepare
from tools.cadconvert.worker import fetch_source, prove, reference, summary


class ProofTests(unittest.TestCase):
    def test_no_request_reads_no_credentials(self):
        from tools.cadconvert.operator import main

        with patch("tools.cadconvert.operator.read_deployment", side_effect=AssertionError):
            self.assertEqual(main([]), 0)

    def test_arbitrary_reports_never_escape(self):
        good = summary("source_incomplete", sourceCoverage="failed")
        self.assertEqual(safe_result(good), good)
        for extra in ({"path": "/private"}, {"dimensions": [1, 2, 3]}, {"message": "raw"}):
            with self.assertRaises(ValueError):
                safe_result({**good, **extra})
        with self.assertRaises(ValueError):
            safe_result({**good, "checks": {**good["checks"], "sourceCoverage": "private text"}})
        self.assertEqual(summary("private error text")["code"], "proof_failed")

    def test_reference_cannot_carry_arbitrary_url_or_unbounded_source(self):
        good = {
            "schemaVersion": 1,
            "jobId": "00000000-0000-4000-8000-000000000001",
            "token": "1900000000." + "a" * 64,
            "expiresAt": "2030-03-17T17:46:40Z",
            "bytes": 7,
            "sha256": hashlib.sha256(b"fixture").hexdigest(),
            "sourceUnit": "mm",
        }
        self.assertEqual(reference(good), good)
        for changed in (
            {"url": "https://other.test"},
            {"bytes": 100000000},
            {"sourceUnit": "guess"},
        ):
            with self.assertRaises(ValueError):
                reference({**good, **changed})

    def test_source_stream_verifies_hash_without_returning_bytes(self):
        class Response:
            status = 200
            headers = {"Content-Length": "7"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self, _):
                value = getattr(self, "done", False)
                self.done = True
                return b"fixture" if not value else b""

        good = {
            "schemaVersion": 1,
            "jobId": "00000000-0000-4000-8000-000000000001",
            "token": "1900000000." + "a" * 64,
            "expiresAt": "2030-03-17T17:46:40Z",
            "bytes": 7,
            "sha256": hashlib.sha256(b"fixture").hexdigest(),
            "sourceUnit": "mm",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch("urllib.request.OpenerDirector.open", return_value=Response()):
                self.assertIsNone(fetch_source(good, Path(directory) / "public.x_t"))
            with patch("urllib.request.OpenerDirector.open", return_value=Response()):
                with self.assertRaises(ValueError):
                    fetch_source({**good, "sha256": "b" * 64}, Path(directory) / "bad.x_t")

    @unittest.skipUnless(
        os.environ.get("CADCONVERT_PUBLIC_BUILD"), "public Rust build not requested"
    )
    def test_public_geometry_reference_and_invalid_inputs(self):
        root = Path(os.environ["CADCONVERT_PUBLIC_BUILD"])
        binary = root / "runner/target/release/auto3d-cadconvert-proof"
        source = root / "upstream/native/crates/cad-convert/tests/samples/small.x_t"
        result = prove(source=source, binary=binary, calibration=True)
        self.assertEqual(result["code"], "reference_validated")
        self.assertTrue(all(v == "passed" for v in result["checks"].values()))
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bad = directory / "truncated.x_t"
            bad.write_bytes(source.read_bytes()[:2000])
            for path, unit in ((source, "guess"), (bad, "m")):
                audit = directory / "audit.json"
                output = directory / "never.glb"
                run = subprocess.run(
                    [str(binary), str(path), unit, str(output), str(audit)],
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(output.exists())
                self.assertEqual(json.loads(audit.read_bytes())["status"], "rejected")

    @unittest.skipUnless(
        os.environ.get("CADCONVERT_PUBLIC_SOURCE"), "public upstream archive not requested"
    )
    def test_build_excludes_vendor_materials_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "prepared"
            prepare(Path(os.environ["CADCONVERT_PUBLIC_SOURCE"]), work)
            assets = work / "upstream/native/crates/cad-ir/assets"
            self.assertEqual([p.name for p in assets.iterdir()], ["solidworks-materials.sldmat"])
            self.assertEqual((assets / "solidworks-materials.sldmat").read_text(), "<materials/>\n")
            self.assertTrue((work / "runner/Cargo.lock").is_file())
            with self.assertRaises(ValueError):
                prepare(Path(os.environ["CADCONVERT_PUBLIC_SOURCE"]), work)


if __name__ == "__main__":
    unittest.main()
