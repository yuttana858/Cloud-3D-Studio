import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from cloud_3d_studio import HTTP_USER_AGENT
from cloud_3d_studio.contracts import ConversionError
from cloud_3d_studio.gateway import connection_application
from cloud_3d_studio.jobs import run_process
from cloud_3d_studio.remote import ARTIFACTS, JobReference, JobTransport, run_owned_job
from deploy import provision_modal


def reference():
    return {
        "jobId": str(uuid4()),
        "attemptId": str(uuid4()),
        "token": "a" * 43,
        "origin": "https://auto3d-5av.pages.dev",
    }


class FakeTransport:
    def __init__(
        self, ref, *, accepted=True, state="running", fail_upload=False, progress_protocol=1
    ):
        self.reference = ref
        self.calls = []
        self.accepted = accepted
        self.state = state
        self.fail_upload = fail_upload
        self.progress_protocol = progress_protocol

    def json(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == "claim":
            return {
                "accepted": self.accepted,
                "progressProtocol": self.progress_protocol,
                "source": {
                    "extension": "step",
                    "bytes": 7,
                    "sha256": hashlib.sha256(b"fixture").hexdigest(),
                    "unit": None,
                },
                "quality": "standard",
            }
        return {"state": self.state}

    def download(self, path, *args, **kwargs):
        path.write_bytes(b"fixture")

    def upload(self, kind, path):
        if self.fail_upload:
            raise ConversionError("transfer_failed", "private diagnostic that must not escape")
        self.calls.append(("upload", kind))
        assert path.is_file()


class OwnedWorkerTests(unittest.TestCase):
    def test_worker_http_identifies_the_application(self):
        transport = JobTransport(JobReference.parse(reference()))
        with patch.object(transport.opener, "open", return_value=io.BytesIO(b"{}")) as request:
            transport.open("claim", "POST", b"{}")
        self.assertEqual(request.call_args.args[0].get_header("User-agent"), HTTP_USER_AGENT)

    def test_reference_has_fixed_origin_and_never_reprs_capability(self):
        body = reference()
        value = JobReference.parse(body)
        self.assertNotIn(body["token"], repr(value))
        for key, bad in (
            ("origin", "http://127.0.0.1"),
            ("jobId", "../escape"),
            ("token", "short"),
        ):
            with self.assertRaises(ConversionError):
                JobReference.parse({**body, key: bad})
        with self.assertRaises(ConversionError):
            JobReference.parse({**body, "sourceUrl": "https://evil.test"})

    def test_owned_success_cleans_files_and_publishes_only_after_all_uploads(self):
        transport = FakeTransport(JobReference.parse(reference()))
        paths = []

        def convert(source, output, unit, quality, **kwargs):
            self.assertEqual(source.read_bytes(), b"fixture")
            self.assertLessEqual(kwargs["limits"].output_bytes, 64 * 1024 * 1024)
            self.assertFalse(kwargs["cancel_check"]())
            output.mkdir()
            paths.append(output.parent)
            for filename in ARTIFACTS.values():
                (output / filename).write_bytes(b"result")

        result = run_owned_job(
            transport.reference, transport_factory=lambda _: transport, converter=convert
        )
        self.assertTrue(result["completed"])
        self.assertEqual(
            [value for operation, value in transport.calls if operation == "upload"],
            list(ARTIFACTS),
        )
        self.assertEqual(transport.calls[-1][0], "complete")
        self.assertFalse(paths[0].exists())
        self.assertNotIn(transport.reference.token, json.dumps(result))

    def test_duplicate_claim_performs_no_conversion(self):
        transport = FakeTransport(JobReference.parse(reference()), accepted=False)
        convert = unittest.mock.Mock()
        self.assertEqual(
            run_owned_job(
                transport.reference, transport_factory=lambda _: transport, converter=convert
            ),
            {"accepted": False},
        )
        convert.assert_not_called()

    def test_cancelled_or_failed_transfer_cannot_publish(self):
        for state, fail_upload in (("cancelling", False), ("running", True)):
            transport = FakeTransport(
                JobReference.parse(reference()), state=state, fail_upload=fail_upload
            )

            def convert(source, output, *args, **kwargs):
                output.mkdir()
                for filename in ARTIFACTS.values():
                    (output / filename).write_bytes(b"result")

            result = run_owned_job(
                transport.reference, transport_factory=lambda _: transport, converter=convert
            )
            self.assertFalse(result["completed"])
            self.assertNotIn("complete", [operation for operation, _ in transport.calls])
            self.assertNotIn("private diagnostic", json.dumps(result))

    def test_bounded_download_checks_actual_size_and_digest(self):
        transport = JobTransport(JobReference.parse(reference()))
        for data, size, checksum in (
            (b"fixture", 7, hashlib.sha256(b"fixture").hexdigest()),
            (b"wrong", 7, "0" * 64),
            (b"too long", 1, "0" * 64),
        ):
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.object(transport, "open", return_value=io.BytesIO(data)),
            ):
                path = Path(directory) / "source.step"
                if data == b"fixture":
                    transport.download(path, size, checksum, 50)
                else:
                    with self.assertRaises(ConversionError):
                        transport.download(path, size, checksum, 50)

    def test_native_cancel_kills_process_group_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            start = time.monotonic()
            with self.assertRaisesRegex(ConversionError, "cancelled"):
                run_process(
                    [sys.executable, "-c", "import time;time.sleep(30)"],
                    Path(directory),
                    20,
                    cancel_check=lambda: True,
                )
            self.assertLess(time.monotonic() - start, 3)

    def test_native_child_has_no_account_or_provider_secrets(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "SUPABASE_SECRET_KEY": "sb_secret_fixture",
                    "MODAL_TOKEN_SECRET": "as-fixture123456",
                    "AUTO3D_CONVERSION_CONTROL_SECRET": "privatefixture",
                },
            ),
        ):
            code = "import os,json,pathlib;pathlib.Path('result.json').write_text(json.dumps({'ok':True,'present':any(k in os.environ for k in ['SUPABASE_SECRET_KEY','MODAL_TOKEN_SECRET','AUTO3D_CONVERSION_CONTROL_SECRET'])}))"
            result = run_process([sys.executable, "-c", code], Path(directory), 3)
            self.assertFalse(result["present"])


class JobGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, path, payload):
        send = AsyncMock()
        receive = AsyncMock(
            return_value={
                "type": "http.request",
                "body": json.dumps(payload).encode(),
                "more_body": False,
            }
        )
        await app({"type": "http", "method": "POST", "path": path}, receive, send)
        values = [call.args[0] for call in send.await_args_list]
        return values[0]["status"], json.loads(values[1]["body"])

    async def test_verified_gateway_dispatches_only_job_references(self):
        dispatch = AsyncMock(return_value="fc-fixture123456")
        app = connection_application(
            {
                "CLOUD3D_WORKSPACE": "fixture",
                "CLOUD3D_ENVIRONMENT": "main",
                "CLOUD3D_NATIVE_VERIFIED": "1",
            },
            dispatch=dispatch,
            cancel=AsyncMock(),
        )
        body = reference()
        status, result = await self.request(app, "/jobs", body)
        self.assertEqual(status, 202)
        dispatch.assert_awaited_once_with(body)
        self.assertNotIn(body["token"], json.dumps(result))
        status, result = await self.request(app, "/jobs", {**body, "origin": "https://evil.test"})
        self.assertEqual(status, 400)
        self.assertEqual(dispatch.await_count, 1)

    async def test_unverified_gateway_never_dispatches(self):
        dispatch = AsyncMock()
        app = connection_application(
            {"CLOUD3D_WORKSPACE": "fixture", "CLOUD3D_ENVIRONMENT": "main"}, dispatch=dispatch
        )
        self.assertEqual((await self.request(app, "/jobs", reference()))[0], 503)
        dispatch.assert_not_awaited()


class ProvisionProofTests(unittest.TestCase):
    def test_gateway_probe_retries_transient_startup_and_withholds_provider_errors(self):
        settings = {"workspace": "fixture", "environment": "main", "app": "cloud-3d-studio"}
        runtime = {"tokenId": "wk-runtimefixture", "tokenSecret": "ws-runtimefixture"}
        identity = {
            **settings,
            "service": "cloud-3d-studio",
            "protocol": 1,
            "version": provision_modal.VERSION,
            "jobApiVersion": 1,
            "conversionReady": True,
        }
        opener = unittest.mock.Mock()
        opener.open.side_effect = [
            urllib.error.HTTPError(
                "https://fixture.modal.run", 503, "private error", {}, io.BytesIO(b"private body")
            ),
            io.BytesIO(json.dumps(identity).encode()),
        ]
        with (
            patch.object(provision_modal.urllib.request, "build_opener", return_value=opener),
            patch.object(provision_modal.time, "sleep"),
        ):
            provision_modal.proxy_probe("https://fixture.modal.run", runtime, settings, True)
        self.assertEqual(opener.open.call_count, 2)
        opener.open.side_effect = [
            io.BytesIO(json.dumps({**identity, "conversionReady": False}).encode()),
            io.BytesIO(json.dumps(identity).encode()),
        ]
        with (
            patch.object(provision_modal.urllib.request, "build_opener", return_value=opener),
            patch.object(provision_modal.time, "sleep") as delay,
        ):
            provision_modal.proxy_probe("https://fixture.modal.run", runtime, settings, True)
        delay.assert_called_once_with(2)
        opener.open.side_effect = urllib.error.HTTPError(
            "https://fixture.modal.run", 403, "private error", {}, io.BytesIO(b"private body")
        )
        with (
            patch.object(provision_modal.urllib.request, "build_opener", return_value=opener),
            patch.object(provision_modal.time, "sleep"),
            self.assertRaises(provision_modal.BridgeError) as failure,
        ):
            provision_modal.proxy_probe("https://fixture.modal.run", runtime, settings)
        self.assertIn("403", str(failure.exception))
        self.assertNotIn("private", str(failure.exception))

    def test_provider_origin_normalizes_one_root_slash_and_rejects_non_origins(self):
        origin = "https://fixture--cad-gateway.modal.run"
        self.assertEqual(provision_modal.gateway_endpoint(origin + "/"), origin)
        regional = "https://fixture--cad-gateway.us-east.modal.run"
        self.assertEqual(provision_modal.gateway_endpoint(regional), regional)
        for value in (
            None,
            origin + "/path",
            origin + "?key=private",
            "https://evil.test",
            "https://user:password@fixture.modal.run",
            "https://fixture--cad-gateway.us-east.evil.modal.run",
        ):
            with self.assertRaises(provision_modal.BridgeError):
                provision_modal.gateway_endpoint(value)

    def test_all_three_artifacts_and_independent_checks_are_required(self):
        proof = {
            "ok": True,
            "version": provision_modal.VERSION,
            "step_reimport_passed": True,
            "mesh_reimport_passed": True,
            "artifacts": ["step", "glb", "stl"],
            "dimensions_mm": [40, 30, 20],
        }
        provision_modal.validate_smoke(proof)
        for change in (
            {"artifacts": ["glb"]},
            {"dimensions_mm": [0.04, 0.03, 0.02]},
            {"version": "old"},
            {"mesh_reimport_passed": False},
            {"dimensions_mm": [float("nan"), 30, 20]},
        ):
            with self.assertRaises(provision_modal.BridgeError):
                provision_modal.validate_smoke({**proof, **change})

    def test_controller_key_is_profile_bound_and_not_runtime_secret(self):
        runtime = {
            "workspace": "fixture",
            "environment": "main",
            "tokenSecret": "ws-privatefixture123456",
        }
        key = provision_modal.control_key("production", runtime)
        self.assertEqual(len(key), 43)
        self.assertNotEqual(key, runtime["tokenSecret"])
        self.assertNotEqual(key, provision_modal.control_key("preview", runtime))


class RuntimeProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def provision(self, mode):
        settings = {
            "workspace": "fixture",
            "environment": "main",
            "app": "cloud-3d-studio",
            "tokenId": "ak-deploymentfixture",
            "tokenSecret": "as-deploymentfixture",
        }
        runtime = {
            **{k: settings[k] for k in ("workspace", "environment", "app")},
            "endpoint": "https://fixture--cloud-3d-studio-cad-gateway.modal.run",
            "tokenId": "wk-runtimefixture",
            "tokenSecret": "ws-runtimefixture",
        }
        proof = {
            "ok": True,
            "version": provision_modal.VERSION,
            "step_reimport_passed": True,
            "mesh_reimport_passed": True,
            "artifacts": ["step", "glb", "stl"],
            "dimensions_mm": [40, 30, 20],
        }
        client = unittest.mock.MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        proxy = SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    token_id=runtime["tokenId"], token_secret=runtime["tokenSecret"]
                )
            ),
            delete=AsyncMock(),
        )
        secret = SimpleNamespace(update=AsyncMock())
        reads = []

        def vault(profile, purpose, action="read", extra=None):
            self.assertEqual((profile, purpose), ("production", "runtime"))
            if action == "save":
                self.assertEqual(extra["p_settings"], runtime)
                if mode != "new":
                    raise provision_modal.BridgeError("Lost save acknowledgement")
                return {"revision": str(uuid4())}
            reads.append(True)
            if mode == "existing" or len(reads) > 1 and mode == "recovered":
                return {"settings": runtime}
            if len(reads) > 1 and mode == "unknown":
                raise provision_modal.BridgeError("Vault unavailable")
            return None

        with (
            patch("modal.client._Client.from_credentials", AsyncMock(return_value=client)),
            patch(
                "modal._functions._Function.from_name",
                side_effect=[
                    SimpleNamespace(remote=AsyncMock(return_value=proof)),
                    SimpleNamespace(get_web_url=AsyncMock(return_value=runtime["endpoint"])),
                ],
            ),
            patch(
                "modal._workspace._Workspace.from_context",
                return_value=SimpleNamespace(proxy_tokens=proxy),
            ),
            patch("modal.secret._SecretManager.create", AsyncMock()) as create_secret,
            patch(
                "modal.secret._Secret.from_name",
                return_value=SimpleNamespace(hydrate=AsyncMock(return_value=secret)),
            ),
            patch.object(provision_modal, "vault_call", side_effect=vault),
            patch.object(provision_modal, "proxy_probe"),
        ):
            if mode in ("unknown", "rejected"):
                with self.assertRaises(provision_modal.BridgeError):
                    await provision_modal.prove_and_provision("production", settings)
                create_secret.assert_not_awaited()
            else:
                result, name = await provision_modal.prove_and_provision("production", settings)
                self.assertEqual(result, runtime)
                self.assertEqual(name, "auto3d-conversion-control-production")
                create_secret.assert_awaited_once()
                secret.update.assert_awaited_once_with(
                    {
                        "AUTO3D_CONVERSION_CONTROL_SECRET": provision_modal.control_key(
                            "production", runtime
                        )
                    }
                )
        self.assertEqual(proxy.create.await_count, 0 if mode == "existing" else 1)
        self.assertEqual(proxy.delete.await_count, 1 if mode == "rejected" else 0)

    async def test_create_and_reuse_saved_runtime(self):
        for mode in ("new", "existing"):
            await self.provision(mode)

    async def test_lost_acknowledgement_never_revokes_a_saved_or_unknown_pair(self):
        for mode in ("recovered", "unknown", "rejected"):
            await self.provision(mode)
