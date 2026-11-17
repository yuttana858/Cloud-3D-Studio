"""Deploy, prove the bounded CPU image, and provision the runtime pair directly into Vault."""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from deploy.vault_modal import (
    BridgeError,
    NoRedirect,
    deployment_environment,
    read_deployment,
    vault_call,
    verify_target,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def deploy(settings, *, verified, controller=None):
    environment = deployment_environment(settings)
    environment["CLOUD3D_NATIVE_VERIFIED"] = "1" if verified else "0"
    environment.pop("CLOUD3D_CONTROL_SECRET_NAME", None)
    if controller:
        environment["CLOUD3D_CONTROL_SECRET_NAME"] = controller
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "deploy",
            "--env",
            settings["environment"],
            "deploy/modal_app.py",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1200,
        check=False,
    )
    if result.returncode:
        raise BridgeError(
            "The Modal image/deployment failed. Inspect restricted provider build logs."
        )


def validate_smoke(result):
    try:
        if (
            result["ok"] is not True
            or result["version"] != VERSION
            or result["step_reimport_passed"] is not True
            or result["mesh_reimport_passed"] is not True
        ):
            raise ValueError()
        if set(result["artifacts"]) != {"step", "glb", "stl"} or any(
            type(a) not in (int, float) or not math.isfinite(a) or abs(a - b) > 1e-5
            for a, b in zip(result["dimensions_mm"], (40, 30, 20), strict=True)
        ):
            raise ValueError()
    except Exception:
        raise BridgeError(
            "The deployed native image did not pass the independent geometry checks."
        ) from None


def gateway_endpoint(value):
    # Modal may return the origin with a trailing slash; store one canonical origin.
    endpoint = value[:-1] if isinstance(value, str) and value.endswith("/") else value or ""
    if not re.fullmatch(
        r"https://[a-z0-9][a-z0-9-]{0,190}(?:\.[a-z0-9][a-z0-9-]{0,62})?\.modal\.run", endpoint
    ):
        parsed = urlsplit(value) if isinstance(value, str) else None
        host = parsed.hostname if parsed else None
        safe_host = host if host and re.fullmatch(r"[a-z0-9_.-]{1,253}", host) else "none"
        raise BridgeError(
            "Modal returned an unsupported gateway URL (type: "
            + type(value).__name__
            + "; host: "
            + safe_host
            + ")."
        )
    return endpoint


def proxy_probe(endpoint, runtime, settings, require_ready=False):
    endpoint = gateway_endpoint(endpoint)
    request = urllib.request.Request(
        endpoint + "/connection",
        headers={"Modal-Key": runtime["tokenId"], "Modal-Secret": runtime["tokenSecret"]},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            data = response.read(16385)
        result = json.loads(data)
        if (
            len(data) > 16384
            or result["service"] != "cloud-3d-studio"
            or result["protocol"] != 1
            or result["version"] != VERSION
            or result["jobApiVersion"] != 1
        ):
            raise ValueError()
        if (
            any(result[key] != settings[key] for key in ("workspace", "environment", "app"))
            or require_ready
            and result["conversionReady"] is not True
        ):
            raise ValueError()
    except Exception:
        raise BridgeError(
            "The authenticated gateway did not return the verified target and protocol."
        ) from None


def control_key(profile, runtime):
    message = (
        f"auto3d-conversion-control:v1:{profile}:{runtime['workspace']}:{runtime['environment']}"
    )
    return (
        base64.urlsafe_b64encode(
            hmac.new(runtime["tokenSecret"].encode(), message.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )


def controller_ready(key):
    request = urllib.request.Request(
        "https://auto3d-5av.pages.dev/internal/conversion-control/health",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            data = response.read(4097)
        return len(data) <= 4096 and json.loads(data).get("ready") is True
    except Exception:
        return False


async def prove_and_provision(profile, settings):
    from modal._functions import _Function
    from modal._workspace import _Workspace
    from modal.client import _Client
    from modal.secret import _Secret

    async with await _Client.from_credentials(
        settings["tokenId"], settings["tokenSecret"]
    ) as client:
        smoke = _Function.from_name(
            settings["app"],
            "cad_smoke_test",
            environment_name=settings["environment"],
            client=client,
        )
        result = await smoke.remote()
        validate_smoke(result)
        print(
            "Deployed native proof passed: 40 x 30 x 20 mm; independent STEP/GLB/STL reimports passed."
        )
        gateway = _Function.from_name(
            settings["app"], "cad_gateway", environment_name=settings["environment"], client=client
        )
        endpoint = gateway_endpoint(await gateway.get_web_url())
        existing = vault_call(profile, "runtime")
        workspace = _Workspace.from_context(client=client)
        created = None
        try:
            if existing:
                runtime = existing["settings"]
                if (
                    any(
                        runtime[key] != settings[key] for key in ("workspace", "environment", "app")
                    )
                    or runtime["endpoint"] != endpoint
                ):
                    raise BridgeError(
                        "The saved runtime connection targets another deployment; it was not replaced."
                    )
            else:
                created = await workspace.proxy_tokens.create(name="auto3d-runtime-" + profile)
                runtime = {key: settings[key] for key in ("workspace", "environment", "app")}
                runtime.update(
                    endpoint=endpoint, tokenId=created.token_id, tokenSecret=created.token_secret
                )
            proxy_probe(endpoint, runtime, settings)
            if created:
                try:
                    vault_call(
                        profile,
                        "runtime",
                        "save",
                        {
                            "p_settings": runtime,
                            "p_expected_revision": None,
                            "p_actor": "00000000-0000-0000-0000-000000000000",
                        },
                    )
                except BridgeError:
                    try:
                        recovered = vault_call(profile, "runtime")
                    except BridgeError:
                        # A lost save acknowledgement may already have committed. Revoking
                        # the token here could invalidate the pair safely stored in Vault.
                        created = None
                        raise BridgeError(
                            "Vault save could not be confirmed. The new token was retained for safe recovery; rerun provisioning."
                        ) from None
                    stored = recovered.get("settings", {}) if isinstance(recovered, dict) else {}
                    if not all(
                        hmac.compare_digest(str(stored.get(field, "")), runtime[field])
                        for field in ("tokenId", "tokenSecret")
                    ):
                        raise
                created = None
            secret_name = "auto3d-conversion-control-" + profile
            values = {"AUTO3D_CONVERSION_CONTROL_SECRET": control_key(profile, runtime)}
            await _Secret.objects.create(
                secret_name,
                values,
                allow_existing=True,
                environment_name=settings["environment"],
                client=client,
            )
            secret = await _Secret.from_name(
                secret_name, environment_name=settings["environment"]
            ).hydrate(client)
            await secret.update(values)
            return runtime, secret_name
        finally:
            if created:
                await workspace.proxy_tokens.delete(created.token_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("production", "preview"))
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Explicitly build and run one bounded synthetic CPU proof.",
    )
    args = parser.parse_args(argv)
    try:
        settings = read_deployment(args.profile)
        asyncio.run(verify_target(settings))
        if not args.deploy:
            print("Vault credentials and target verified; deployment was not requested.")
            return 0
        deploy(settings, verified=False)
        runtime, secret_name = asyncio.run(prove_and_provision(args.profile, settings))
        enable_control = controller_ready(control_key(args.profile, runtime))
        deploy(settings, verified=True, controller=secret_name if enable_control else None)
        proxy_probe(runtime["endpoint"], runtime, settings, require_ready=True)
        print(
            "Native STEP/GLB/STL proof passed; protected job gateway is ready; runtime credentials are saved in Vault."
        )
        print(
            "Background reconciliation is enabled."
            if enable_control
            else "Background reconciliation will enable after the Auto3D connector is live."
        )
        return 0
    except BridgeError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print(
            "Modal provisioning failed; provider details and credentials were withheld.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
