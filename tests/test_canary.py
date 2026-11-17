import hashlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from cloud_3d_studio.remote import reconcile_control
from deploy import canary_modal


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
            return {"ready": True}
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
                },
            }
        }


class CanaryTests(unittest.TestCase):
    def test_explicit_success_validates_all_files_and_removes_both_accounts(self):
        fake = FakeCanary()
        result = canary_modal.run_canary(fake)
        self.assertTrue(result["passed"])
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
            self.assertEqual(
                opener.open.call_args_list[0].args[0].full_url,
                "https://auto3d-5av.pages.dev/internal/conversion-control/health",
            )
