import io
import unittest
from unittest.mock import patch

from tools import check_conversion_pipeline as check


class PipelineCheckTests(unittest.TestCase):
    def test_default_does_not_access_credentials(self):
        with (
            patch.object(check, "vault_call") as vault,
            patch.object(check.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(check.main([]), 0)
            vault.assert_not_called()

    def test_failed_operation_excludes_path_ids_body_and_tokens(self):
        with (
            patch.object(check.CanaryClient, "__init__", return_value=None),
            patch.object(
                check.CanaryClient, "api", side_effect=check.CanaryError("HTTP 503 private-body")
            ),
            patch.object(check.sys, "stderr", io.StringIO()) as output,
        ):
            with self.assertRaises(check.CanaryError):
                check.TracedClient().api({}, "/private-job/artifacts/glb")
            self.assertEqual(output.getvalue(), "Pipeline GET api.artifact.glb failed. HTTP 503\n")
            self.assertNotIn("private", output.getvalue())

    def test_ready_check_reuses_current_gateway_and_full_canary(self):
        settings = {"endpoint": "https://fixture.modal.run"}
        with (
            patch.object(check, "vault_call", return_value={"settings": settings}),
            patch.object(check, "proxy_probe") as gateway,
            patch.object(check, "control_key", return_value="private-key"),
            patch.object(check, "controller_ready", return_value=True),
            patch.object(check, "TracedClient"),
            patch.object(
                check,
                "run_canary",
                return_value={"serverDiagnostics": True, "progressTelemetry": True},
            ) as canary,
            patch.object(check.sys, "stdout", io.StringIO()) as output,
        ):
            self.assertEqual(check.main(["--run"]), 0)
            gateway.assert_called_once_with(
                settings["endpoint"], settings, settings, require_ready=True
            )
            canary.assert_called_once()
            self.assertNotIn("private-key", output.getvalue())

    def test_missing_readiness_is_failure_and_omits_private_error(self):
        with (
            patch.object(check, "vault_call", side_effect=RuntimeError("private-token")),
            patch.object(check.sys, "stderr", io.StringIO()) as output,
        ):
            self.assertEqual(check.main(["--run"]), 1)
            self.assertNotIn("private-token", output.getvalue())
