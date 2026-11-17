import hashlib
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from cloud_3d_studio import HTTP_USER_AGENT
from cloud_3d_studio.remote import reconcile_control
from deploy import canary_modal, provision_modal


class FakeCanary:
    def __init__(self, *, leak=False, corrupt=False, cleanup_fail=False):
        self.accounts = []
        self.deleted = []
        self.job_id = str(uuid4())
        self.submitted = False
        self.leak = leak
        self.corrupt = corrupt
        self.cleanup_fail = cleanup_fail
        self.files = {"step": b"public-step", "glb": b"public-glb", "stl": b"public-stl"}
        self.deleted_job = False

    def account(self):
        account = {"owner": str(uuid4()), "token": "private-test-access-token"}
        self.accounts.append(account)
        return account

    def supabase(self, path, method):
        self.assert_delete(path, method)
        self.deleted.append(path)
        if self.cleanup_fail:
            raise RuntimeError("private diagnostic")

    def assert_delete(self, path, method):
        assert path.startswith("admin/users/") and method == "DELETE"

    def api(self, account, path="", method="GET", body=None, binary=False):
        if path == "/capabilities":
            return {"ready": True, "progressProtocol": 1}
        if method == "DELETE":
            self.deleted_job = True
            return {"deleted": True}
        if account == self.accounts[1] and not self.leak:
            raise canary_modal.CanaryError("Canary request failed with HTTP 404.")
        if not path:
            return {"job": {"id": self.job_id}}
        if path.endswith("/source"):
            self.source_hash = hashlib.sha256(body).hexdigest()
            return {"uploaded": True}
        if path.endswith("/submit"):
            self.submitted = True
            return {"job": {"state": "queued"}}
        if "/artifacts/" in path:
            kind = path.rsplit("/", 1)[-1]
            if kind == "manifest":
                return json.dumps(
                    {
                        "source": {"sha256": self.source_hash},
                        "artifacts": {
                            key: {
                                "bytes": len(value),
                                "sha256": "0" * 64
                                if self.corrupt
                                else hashlib.sha256(value).hexdigest(),
                                "units": "m" if key == "glb" else "mm",
                            }
                            for key, value in self.files.items()
                        },
                    }
                ).encode()
            return self.files[kind]
        return {
            "job": {
                "state": "ready" if self.submitted else "awaiting_upload",
                "revision": 1,
                "summary": {
                    "dimensionsMm": [40, 30, 20],
                    "stepReimportPassed": True,
                    "meshReimportPassed": True,
                    "progressReported": True,
                },
            }
        }


class CanaryTests(unittest.TestCase):
    def test_diagnostic_canary_reuses_upload_and_always_removes_the_test_job(self):
        from cloud_3d_studio.contracts import ConversionError
        from cloud_3d_studio.diagnostics import diagnostic_report

        for mode in ("success", "leak", "missing_report", "not_queued"):
            primary, outsider = object(), object()
            state = {"phase": "awaiting_upload", "uploads": 0, "deleted": False, "diagnoses": 0}
            job_id = str(uuid4())
            report = diagnostic_report(
                ConversionError("invalid_parasolid", "private"), {"phase": "read_source"}
            )

            def api(account, path="", method="GET", body=None, binary=False):
                self.assertNotIn("/artifacts/", path)
                if method == "DELETE":
                    state["deleted"] = True
                    return {"deleted": True}
                if account is outsider:
                    if mode != "leak":
                        raise canary_modal.CanaryError("Canary request failed with HTTP 404.")
                    return {"job": {}}
                if not path:
                    state["hash"] = body["sourceSha256"]
                    return {"job": {"id": job_id}}
                if path.endswith("/source"):
                    self.assertEqual(hashlib.sha256(body).hexdigest(), state["hash"])
                    state["uploads"] += 1
                    return {"uploaded": True}
                if path.endswith("/submit"):
                    state["phase"] = "failed"
                    return {"job": {}}
                if path.endswith("/diagnose"):
                    state["diagnoses"] += 1
                    return {
                        "job": {"purpose": "conversion" if mode == "not_queued" else "diagnostic"}
                    }
                return {
                    "job": {
                        "state": state["phase"],
                        "revision": 1,
                        "errorCode": "invalid_parasolid",
                        "source": {"sha256": state["hash"]},
                        "diagnostics": {"report": {} if mode == "missing_report" else report},
                    }
                }

            fake = SimpleNamespace(api=api)
            if mode == "success":
                self.assertTrue(canary_modal.run_diagnostic_canary(fake, primary, outsider))
                self.assertEqual(state["uploads"], 1)
                self.assertEqual(state["diagnoses"], 1)
            else:
                with self.assertRaises(canary_modal.CanaryError):
                    canary_modal.run_diagnostic_canary(fake, primary, outsider)
            self.assertTrue(state["deleted"])

    def test_readiness_diagnostics_do_not_print_response_or_credentials(self):
        for outcome, expected in (
            (io.BytesIO(b'{"ready":true}'), "ready."),
            (io.BytesIO(b"private-provider-body"), "invalid response."),
            (
                urllib.error.HTTPError("https://fixture", 503, "private-details", {}, None),
                "HTTP 503.",
            ),
        ):
            output = io.StringIO()
            opener = Mock()
            if isinstance(outcome, Exception):
                opener.open.side_effect = outcome
            else:
                opener.open.return_value = outcome
            with (
                patch.object(provision_modal.urllib.request, "build_opener", return_value=opener),
                redirect_stdout(output),
            ):
                ready = provision_modal.controller_ready("private-control-key")
            self.assertEqual(ready, expected == "ready.")
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("private", output.getvalue())
            self.assertEqual(
                opener.open.call_args.args[0].get_header("User-agent"), HTTP_USER_AGENT
            )

    def test_canary_http_identifies_the_application(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"ready":true}')
        with (
            patch.object(
                canary_modal,
                "supabase_connection",
                return_value=("https://fixture.supabase.co", {}),
            ),
            patch.object(canary_modal.urllib.request, "build_opener", return_value=opener),
        ):
            client = canary_modal.CanaryClient()
            client.request("https://auto3d-5av.pages.dev/internal/conversion-control/health")
        self.assertEqual(opener.open.call_args.args[0].get_header("User-agent"), HTTP_USER_AGENT)

    def test_explicit_success_validates_all_files_and_removes_both_accounts(self):
        fake = FakeCanary()
        result = canary_modal.run_canary(fake)
        self.assertTrue(result["passed"])
        self.assertTrue(result["progressTelemetry"])
        self.assertEqual(len(fake.deleted), 2)
        self.assertTrue(fake.deleted_job)
        self.assertNotIn("private", json.dumps(result))

    def test_isolation_or_hash_failure_still_cleans_every_temporary_identity(self):
        for options in ({"leak": True}, {"corrupt": True}):
            fake = FakeCanary(**options)
            with self.assertRaises(canary_modal.CanaryError):
                canary_modal.run_canary(fake)
            self.assertEqual(len(fake.deleted), 2)
            self.assertTrue(fake.deleted_job)

    def test_cleanup_failure_cannot_report_success_or_echo_provider_details(self):
        fake = FakeCanary(cleanup_fail=True)
        with self.assertRaises(canary_modal.CanaryError) as failure:
            canary_modal.run_canary(fake)
        self.assertIn("cleanup", str(failure.exception))
        self.assertNotIn("private diagnostic", str(failure.exception))
        self.assertEqual(len(fake.deleted), 2)

    def test_missing_negotiated_progress_fails_and_still_cleans_test_data(self):
        class MissingProgress(FakeCanary):
            def api(self, *args, **kwargs):
                result = super().api(*args, **kwargs)
                if isinstance(result, dict) and result.get("job", {}).get("summary"):
                    result["job"]["summary"].pop("progressReported", None)
                return result

        fake = MissingProgress()
        with self.assertRaisesRegex(canary_modal.CanaryError, "progress telemetry"):
            canary_modal.run_canary(fake)
        self.assertEqual(len(fake.deleted), 2)
        self.assertTrue(fake.deleted_job)

    def test_default_cli_performs_no_account_or_network_mutation(self):
        with (
            patch.object(canary_modal, "CanaryClient") as client,
            patch.object(canary_modal.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(canary_modal.main([]), 0)
            client.assert_not_called()

    def test_controller_waits_for_health_before_reconciling(self):
        for ready in (False, True):
            health = io.BytesIO(json.dumps({"ready": ready}).encode())
            accepted = Mock()
            accepted.__enter__ = Mock(return_value=SimpleNamespace(status=200))
            accepted.__exit__ = Mock(return_value=False)
            opener = Mock()
            opener.open.side_effect = [health, accepted]
            with patch("cloud_3d_studio.remote.urllib.request.build_opener", return_value=opener):
                self.assertEqual(reconcile_control("private-control-fixture"), {"accepted": ready})
            self.assertEqual(opener.open.call_count, 2 if ready else 1)
            for call in opener.open.call_args_list:
                self.assertEqual(call.args[0].get_header("User-agent"), HTTP_USER_AGENT)
            self.assertEqual(
                opener.open.call_args_list[0].args[0].full_url,
                "https://auto3d-5av.pages.dev/internal/conversion-control/health",
            )
