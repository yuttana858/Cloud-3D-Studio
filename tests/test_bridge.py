import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cloud_3d_studio.gateway import connection_application
from deploy import vault_modal


def settings():
    return {
        "workspace": "acme",
        "environment": "main",
        "app": "cloud-3d-studio",
        "endpoint": "",
        "tokenId": "ak-fixture123456",
        "tokenSecret": "as-privatefixture123456",
    }


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, path="/connection", method="GET"):
        events = []

        async def send(event):
            events.append(event)

        await app({"type": "http", "path": path, "method": method}, AsyncMock(), send)
        return events[0]["status"], json.loads(events[1]["body"])

    async def test_identity_and_no_job_submission(self):
        app = connection_application({"CLOUD3D_WORKSPACE": "acme", "CLOUD3D_ENVIRONMENT": "main"})
        status, result = await self.request(app)
        self.assertEqual(status, 200)
        self.assertEqual(result["workspace"], "acme")
        self.assertEqual(result["environment"], "main")
        self.assertFalse(result["conversionReady"])
        self.assertEqual((await self.request(app, "/jobs", "POST"))[0], 503)
        self.assertEqual((await self.request(app, method="POST"))[0], 405)

    async def test_unconfigured_identity_fails_closed(self):
        self.assertEqual((await self.request(connection_application({})))[0], 503)

    async def test_lifespan(self):
        app = connection_application({})
        receive = AsyncMock(
            side_effect=[{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        )
        send = AsyncMock()
        await app({"type": "lifespan"}, receive, send)
        self.assertEqual(send.await_count, 2)

    def test_modal_declaration_is_proxy_protected_and_cpu_only(self):
        from modal._utils.async_utils import synchronizer
        from modal_proto import api_pb2

        from deploy.modal_app import cad_gateway

        function = synchronizer._translate_in(cad_gateway)
        self.assertTrue(function._webhook_config.requires_proxy_auth)
        self.assertEqual(function._webhook_config.type, api_pb2.WEBHOOK_TYPE_ASGI_APP)


class VaultRunnerTests(unittest.TestCase):
    environ = {
        "SUPABASE_URL": "https://fixture.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_fixture",
    }

    def read(self, record):
        response = io.BytesIO(json.dumps(record).encode())
        opener = SimpleNamespace(open=lambda *args, **kwargs: response)
        with patch.object(vault_modal.urllib.request, "build_opener", return_value=opener):
            return vault_modal.read_deployment("preview", self.environ)

    def test_read_complete_bound_deployment_record(self):
        value = self.read(
            {"schema": 1, "profile": "preview", "purpose": "deployment", "settings": settings()}
        )
        self.assertEqual(value, settings())

    def test_secret_key_header_is_not_a_jwt_and_browser_keys_are_rejected(self):
        record = {
            "schema": 1,
            "profile": "preview",
            "purpose": "deployment",
            "settings": settings(),
        }
        captured = []

        def open_request(request, **kwargs):
            captured.append(request)
            return io.BytesIO(json.dumps(record).encode())

        with patch.object(
            vault_modal.urllib.request,
            "build_opener",
            return_value=SimpleNamespace(open=open_request),
        ):
            vault_modal.read_deployment("preview", self.environ)
        self.assertEqual(captured[0].get_header("Apikey"), "sb_secret_fixture")
        self.assertIsNone(captured[0].get_header("Authorization"))
        with self.assertRaises(vault_modal.BridgeError):
            vault_modal.read_deployment(
                "preview", {**self.environ, "SUPABASE_SECRET_KEY": "sb_publishable_fixture"}
            )

    def test_reject_swapped_tokens_copied_profiles_missing_and_untrusted_urls(self):
        base = {"schema": 1, "profile": "preview", "purpose": "deployment", "settings": settings()}
        for record in [
            None,
            {**base, "profile": "production"},
            {**base, "purpose": "runtime"},
            {**base, "settings": {**settings(), "tokenId": "wk-fixture123456"}},
            {**base, "settings": {**settings(), "tokenSecret": ""}},
        ]:
            with self.assertRaises(vault_modal.BridgeError):
                self.read(record)
        for url in ("http://fixture.supabase.co", "https://fixture.supabase.co.evil.test"):
            with self.assertRaises(vault_modal.BridgeError):
                vault_modal.read_deployment("preview", {**self.environ, "SUPABASE_URL": url})

    def test_redirect_is_not_followed(self):
        with self.assertRaises(vault_modal.BridgeError):
            vault_modal.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test")

    def test_child_gets_one_pair_and_no_supabase_or_alternate_auth(self):
        child = vault_modal.deployment_environment(
            settings(),
            {
                **self.environ,
                "MODAL_TOKEN_ID": "ak-stale-token",
                "MODAL_OAUTH_REFRESH_TOKEN": "stale-refresh",
                "MODAL_SERVER_URL": "https://evil.test",
                "PATH": "/fixture/bin",
            },
        )
        self.assertEqual(child["MODAL_TOKEN_ID"], settings()["tokenId"])
        self.assertEqual(child["CLOUD3D_WORKSPACE"], "acme")
        self.assertEqual(child["PATH"], "/fixture/bin")
        self.assertFalse(any(key.startswith("SUPABASE_") for key in child))
        self.assertNotIn("MODAL_OAUTH_REFRESH_TOKEN", child)
        self.assertNotIn("MODAL_SERVER_URL", child)

    @patch.object(vault_modal, "verify_target", new_callable=AsyncMock)
    @patch.object(vault_modal, "read_deployment", return_value=settings())
    @patch.object(vault_modal.subprocess, "run")
    def test_check_default_never_deploys_or_prints_tokens(self, run, read, verify):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(vault_modal.main(["--profile", "preview"]), 0)
        run.assert_not_called()
        self.assertNotIn(settings()["tokenSecret"], output.getvalue())

    @patch.object(vault_modal, "verify_target", new_callable=AsyncMock)
    @patch.object(vault_modal, "read_deployment", return_value=settings())
    @patch.object(vault_modal.subprocess, "run", return_value=subprocess.CompletedProcess([], 0))
    def test_explicit_deployment_and_redacted_failure(self, run, read, verify):
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(vault_modal.main(["--profile", "preview", "--deploy"]), 0)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"]["MODAL_TOKEN_SECRET"], settings()["tokenSecret"])
        self.assertNotIn(settings()["tokenSecret"], str(run.call_args.args[0]))
        verify.side_effect = RuntimeError(settings()["tokenSecret"])
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(vault_modal.main(["--profile", "preview", "--deploy"]), 1)
        self.assertNotIn(settings()["tokenSecret"], output.getvalue())


class TargetVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_and_environment_are_verified_before_deploy(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client._stub.TokenInfoGet = AsyncMock(return_value=SimpleNamespace(workspace_name="acme"))
        with (
            patch("modal.client._Client.from_credentials", new=AsyncMock(return_value=client)),
            patch(
                "modal._environments._list_environments",
                new=AsyncMock(return_value=[SimpleNamespace(name="main")]),
            ),
        ):
            await vault_modal.verify_target(settings())
            with self.assertRaisesRegex(vault_modal.BridgeError, "different Modal workspace"):
                await vault_modal.verify_target({**settings(), "workspace": "other"})
            with self.assertRaisesRegex(vault_modal.BridgeError, "environment is unavailable"):
                await vault_modal.verify_target({**settings(), "environment": "other"})
