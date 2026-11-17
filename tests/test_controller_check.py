import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools import check_conversion_controller as check


class ControllerCheckTests(unittest.TestCase):
    def test_default_never_reads_credentials_or_changes_jobs(self):
        with (
            patch.object(check, "vault_call") as vault,
            patch.object(check.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(check.main([]), 0)
            vault.assert_not_called()

    def test_failed_post_reports_no_private_response_details(self):
        with (
            patch.object(check, "vault_call", return_value={"settings": {}}),
            patch.object(check, "control_key", return_value="private-key"),
            patch.object(
                check,
                "request_control",
                side_effect=[
                    {"ready": True},
                    check.BridgeError("Controller reconcile returned HTTP 503."),
                ],
            ),
            patch.object(check, "probe_filters") as probe,
            patch.object(check.sys, "stderr", io.StringIO()) as output,
        ):
            self.assertEqual(check.main(["--run"]), 1)
            probe.assert_called_once()
            self.assertNotIn("private", output.getvalue())

    def test_success_checks_the_deployed_controller_without_redeploying(self):
        with (
            patch.object(check, "vault_call", return_value={"settings": {}}),
            patch.object(check, "control_key", return_value="private-key"),
            patch.object(
                check, "request_control", side_effect=[{"ready": True}, {"accepted": True}]
            ),
            patch.object(check, "read_deployment", return_value={}),
            patch.object(check, "verify_target", new_callable=AsyncMock),
            patch.object(check, "diagnose_controller", new_callable=AsyncMock),
            patch.object(check, "verify_controller", new_callable=AsyncMock) as invoke,
            patch.object(check.sys, "stdout", io.StringIO()) as output,
        ):
            self.assertEqual(check.main(["--run"]), 0)
            invoke.assert_awaited_once()
            self.assertNotIn("private-key", output.getvalue())

    def test_filter_probe_drops_all_returned_job_identifiers(self):
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(b'[{"id":"private-job-identifier"}]') for _ in range(5)
        ]
        with (
            patch.object(
                check, "supabase_connection", return_value=("https://fixture.supabase.co", {})
            ),
            patch.object(check.urllib.request, "build_opener", return_value=opener),
            patch.object(check.sys, "stdout", io.StringIO()) as output,
        ):
            check.probe_filters()
            self.assertEqual(output.getvalue().count("HTTP 200"), 5)
            self.assertNotIn("private-job", output.getvalue())

    def test_startup_labels_exclude_arbitrary_log_text_and_paths(self):
        labels = check.startup_labels(
            "ModuleNotFoundError: No module named 'cloud_3d_studio'\n"
            "FileNotFoundError: /root/deploy/requirements-worker.lock\n"
            'File "/private/modal_app.py", line 14\n'
            "RuntimeError: private-token private-cad-description\n"
            "ModuleNotFoundError: No module named 'private_customer'"
            "\nmodal.exception.ExecutionError: Function has 2 dependencies but container got 3 object ids."
        )
        self.assertIn("missing module cloud_3d_studio", labels)
        self.assertIn("missing deployment file requirements-worker.lock", labels)
        self.assertIn("modal_app:14", labels)
        self.assertIn("dependency mismatch 2/3", labels)
        self.assertNotIn("private", str(labels))
