import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parasolid_kit.diagnostics import Diagnostic, DiagnosticKind, DiagnosticSeverity
from parasolid_kit.errors import ParseError, SchemaError
from test_owned_jobs import FakeTransport, reference

from cloud_3d_studio import worker
from cloud_3d_studio.failures import failure_code, failure_context
from cloud_3d_studio.progress import ProgressFile, progress_value, read_progress
from cloud_3d_studio.remote import JobReference, run_owned_job


class ProgressTests(unittest.TestCase):
    def test_progress_payload_negotiates_with_older_pages_before_deployment(self):
        for protocol in (None, 1):
            transport = FakeTransport(JobReference.parse(reference()), progress_protocol=protocol)

            def convert(source, output, *args, **kwargs):
                from cloud_3d_studio.remote import ARTIFACTS

                output.mkdir()
                for filename in ARTIFACTS.values():
                    (output / filename).write_bytes(b"public-result")
                kwargs["on_progress"](progress_value("build_mesh", 1, 1, "faces"))

            result = run_owned_job(
                transport.reference, transport_factory=lambda _: transport, converter=convert
            )
            self.assertTrue(result["completed"])
            calls = [body for operation, body in transport.calls if operation == "progress"]
            self.assertTrue(calls)
            self.assertEqual(all("progress" in body for body in calls), protocol == 1)

    def test_counts_are_measured_bounded_and_geometry_free(self):
        self.assertEqual(progress_value("build_mesh", 3, 12, "faces")["completed"], 3)
        for args in (
            ("unknown",),
            ("build_mesh", -1, 4, "faces"),
            ("build_mesh", 5, 4, "faces"),
            ("build_mesh", 1, 0, "faces"),
            ("build_mesh", True, 4, "faces"),
        ):
            with self.assertRaises(ValueError):
                progress_value(*args)

    def test_atomic_progress_file_never_accepts_private_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress = ProgressFile(root)
            progress("read_source")
            self.assertEqual(read_progress(root)["phase"], "read_source")
            progress("build_mesh", 4, 4, "faces")
            self.assertEqual(read_progress(root)["total"], 4)
            progress.path.write_text(json.dumps({**progress.latest, "filename": "private-cad"}))
            self.assertIsNone(read_progress(root))

    def test_schema_and_parse_errors_keep_fixed_actionable_codes(self):
        diagnostic = Diagnostic(
            code="schema.missing",
            severity=DiagnosticSeverity.ERROR,
            kind=DiagnosticKind.UNSUPPORTED,
            message="private source geometry",
        )
        self.assertEqual(failure_code(SchemaError(diagnostic)), "unsupported_schema")
        self.assertEqual(failure_code(ParseError(diagnostic)), "unsupported_entity")
        self.assertEqual(failure_code(MemoryError("private")), "complexity_limit")
        self.assertEqual(failure_code(TypeError("private")), "worker_failed")
        self.assertNotIn("private", json.dumps(failure_context(TypeError("private"))))

    def test_child_error_does_not_publish_exception_text_or_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text(json.dumps({"limits": {}}))
            with (
                patch.object(worker.sys, "argv", ["worker", str(request)]),
                patch.object(worker, "apply_limits"),
                patch.object(
                    worker, "execute", side_effect=TypeError("private-geometry-and-filename")
                ),
            ):
                self.assertEqual(worker.main(), 1)
            result = json.loads((root / "result.json").read_text())
            self.assertEqual(result["error"]["code"], "worker_failed")
            self.assertEqual(result["error"]["diagnostic"]["exception"], "TypeError")
            self.assertNotIn("private-geometry", json.dumps(result))
            self.assertNotIn(directory, json.dumps(result))

    def test_chained_geometry_failures_retain_only_the_root_category_and_public_code(self):
        try:
            exec(
                compile(
                    "raise TypeError('private geometry')",
                    "/opt/parasolid_kit/interop/occt/builders.py",
                    "exec",
                )
            )
        except TypeError as cause:
            try:
                raise RuntimeError("private source filename") from cause
            except RuntimeError as error:
                diagnostic = failure_context(error)
                self.assertEqual(diagnostic["exception"], "TypeError")
                self.assertEqual(diagnostic["frames"], ["builders:1"])
                self.assertNotIn("private", json.dumps(diagnostic))
                cause.__cause__ = error  # Malformed chains cannot loop forever.
                self.assertEqual(failure_context(error), diagnostic)
