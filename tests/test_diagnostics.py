import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from test_owned_jobs import FakeTransport, reference

from cloud_3d_studio.contracts import ConversionError
from cloud_3d_studio.diagnostics import diagnostic_report
from cloud_3d_studio.jobs import diagnose_file
from cloud_3d_studio.native import write_step
from cloud_3d_studio.remote import JobReference, run_owned_job


class DiagnosticTransport(FakeTransport):
    def __init__(self, *args, purpose="diagnostic", protocol=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.purpose, self.protocol = purpose, protocol

    def json(self, operation, payload):
        result = super().json(operation, payload)
        if operation == "claim":
            result.update(purpose=self.purpose, diagnosticProtocol=self.protocol)
        return result


class DiagnosticTests(unittest.TestCase):
    def test_report_keeps_only_public_root_category_code_locations_and_checks(self):
        root_type = type("Standard_ConstructionError", (RuntimeError,), {})
        try:
            exec(
                compile("raise error", "/public/parasolid_kit/interop/occt/surfaces.py", "exec"),
                {"error": root_type("private geometry and customer name")},
            )
        except root_type as cause:
            try:
                wrapper = RuntimeError("private filename and details")
                wrapper.diagnostic = SimpleNamespace(
                    code="occt.construction_failed",
                    message="private",
                    details={"secret": "private"},
                )
                raise wrapper from cause
            except RuntimeError as error:
                report = diagnostic_report(error, {"phase": "build_geometry"})
        self.assertEqual(report["exception"], "Standard_ConstructionError")
        self.assertEqual(report["readerCode"], "occt.construction_failed")
        self.assertIn("surfaces:1", report["frames"])
        self.assertEqual(report["checks"]["geometryBuild"], "failed")
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("/public/", json.dumps(report))

    def test_actual_server_diagnosis_of_valid_step_publishes_no_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private-name.step"
            write_step(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), source)
            report = diagnose_file(source)
            self.assertEqual(report["outcome"], "cad_validated")
            self.assertEqual(set(report["checks"].values()), {"passed"})
            self.assertNotIn("dimensions", json.dumps(report))
            self.assertNotIn("private-name", json.dumps(report))
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_actual_invalid_parasolid_reports_a_failed_read_without_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private-name.x_t"
            source.write_bytes(b"private customer content is not a Parasolid document")
            report = diagnose_file(source, "mm")
            self.assertEqual(report["outcome"], "error_detected")
            self.assertEqual(report["failureCode"], "invalid_parasolid")
            self.assertEqual(report["checks"]["sourceRead"], "failed")
            self.assertNotIn("private", json.dumps(report))

    def test_diagnostic_job_returns_only_status_to_modal_and_never_uploads_artifacts(self):
        transport = DiagnosticTransport(JobReference.parse(reference()))
        report = diagnostic_report(
            ConversionError("invalid_geometry", "private"), {"phase": "build_geometry"}
        )

        def forbidden(*args, **kwargs):
            self.fail("Diagnostic job attempted conversion/artifact publication")

        result = run_owned_job(
            transport.reference,
            transport_factory=lambda _: transport,
            converter=forbidden,
            diagnoser=lambda *args, **kwargs: report,
        )
        self.assertTrue(result["completed"])
        self.assertNotIn("report", result)
        self.assertFalse(any(operation == "upload" for operation, _ in transport.calls))
        self.assertEqual(
            [body for operation, body in transport.calls if operation == "complete"],
            [{"report": report}],
        )

    def test_automatic_failure_report_requires_negotiation_with_pages(self):
        for protocol in (None, 1):
            transport = DiagnosticTransport(
                JobReference.parse(reference()), purpose="conversion", protocol=protocol
            )

            def fail(*args, **kwargs):
                error = ConversionError("invalid_geometry", "private")
                error.report = diagnostic_report(error, {"phase": "build_geometry"})
                raise error

            result = run_owned_job(
                transport.reference, transport_factory=lambda _: transport, converter=fail
            )
            self.assertFalse(result["completed"])
            payload = [body for operation, body in transport.calls if operation == "failure"][0]
            self.assertEqual("report" in payload, protocol == 1)
            self.assertNotIn("private", json.dumps(payload))
