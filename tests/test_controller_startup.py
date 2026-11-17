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
    def test_container_image_environment_reconstructs_the_same_secret_dependencies(self):
        environment = dict(os.environ)
        environment["CLOUD3D_CONTROL_SECRET_NAME"] = "fixture-controller"
        environment.pop("AUTO3D_CONVERSION_CONTROL_SECRET", None)
        code = """
import importlib
import os
import modal
from modal._utils.async_utils import synchronizer
original_env = modal.Image.env
captured = []
def capture(image, values):
    captured.append(dict(values))
    return original_env(image, values)
modal.Image.env = capture
import deploy.modal_app as module
def dependencies():
    function = synchronizer._translate_in(module.cad_reconcile)
    return tuple(type(value).__name__ for value in function._deps_())
deployed = dependencies()
assert '_Secret' in deployed
image_environment = captured[-1]
assert 'AUTO3D_CONVERSION_CONTROL_SECRET' not in image_environment
os.environ.pop('CLOUD3D_CONTROL_SECRET_NAME')
os.environ.update(image_environment)
importlib.reload(module)
assert dependencies() == deployed
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, "Controller dependency reconstruction failed.")

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
