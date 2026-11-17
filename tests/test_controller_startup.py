import asyncio
import io
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from deploy import provision_modal


class ControllerStartupTests(unittest.TestCase):
    def test_remote_import_defines_the_function_without_the_deploy_environment(self):
        environment = dict(os.environ)
        environment.pop("CLOUD3D_CONTROL_SECRET_NAME", None)
        environment.pop("AUTO3D_CONVERSION_CONTROL_SECRET", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from deploy.modal_app import cad_reconcile; "
                "assert cad_reconcile.local() == {'accepted': False}",
            ],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, "Remote module import/startup failed.")

    def test_enabled_deployment_exercises_the_actual_remote_controller(self):
        with (
            patch.object(provision_modal, "read_deployment", return_value={}),
            patch.object(provision_modal, "verify_target", new_callable=AsyncMock),
            patch.object(provision_modal, "deploy") as deploy,
            patch.object(
                provision_modal,
                "prove_and_provision",
                new_callable=AsyncMock,
                return_value=({"endpoint": "https://fixture.modal.run"}, "fixture-control"),
            ),
            patch.object(provision_modal, "control_key", return_value="private-key"),
            patch.object(provision_modal, "controller_ready", return_value=True),
            patch.object(provision_modal, "proxy_probe"),
            patch.object(provision_modal, "verify_controller", new_callable=AsyncMock) as verify,
            patch.object(provision_modal.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(provision_modal.main(["--profile", "production", "--deploy"]), 0)
            verify.assert_awaited_once_with({})
            self.assertEqual(deploy.call_count, 2)


class ControllerInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_verification_requires_an_accepted_result_and_bounds_startup(self):
        settings = {
            "tokenId": "fixture-id",
            "tokenSecret": "private-key",
            "app": "cloud-3d-studio",
            "environment": "main",
        }
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        for result in ({"accepted": True}, {"accepted": False}, "private-response"):
            with (
                patch("modal.client._Client.from_credentials", AsyncMock(return_value=client)),
                patch(
                    "modal._functions._Function.from_name",
                    return_value=SimpleNamespace(remote=AsyncMock(return_value=result)),
                ),
                patch.object(
                    provision_modal.asyncio, "wait_for", wraps=asyncio.wait_for
                ) as bounded,
            ):
                if isinstance(result, dict) and result.get("accepted"):
                    await provision_modal.verify_controller(settings)
                else:
                    with self.assertRaises(provision_modal.BridgeError):
                        await provision_modal.verify_controller(settings)
                self.assertEqual(bounded.call_args.kwargs, {"timeout": 90})
