"""Read one Modal deployment pair from Auto3D's Vault; check by default, deploy explicitly.

Run only on a trusted machine/runner with the existing Supabase server key. No tokens
are printed, passed as command arguments, written to profiles, or sent to a browser.
"""

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


class BridgeError(Exception):
    """Only fixed, credential-free messages may leave this module."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("The Vault endpoint redirected; credentials were not forwarded.")


def supabase_connection(environ=None):
    environ = os.environ if environ is None else environ
    url = environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (
        environ.get("SUPABASE_SECRET_KEY") or environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    ).strip()
    if not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co", url) or not key:
        raise BridgeError("Configure the trusted runner's Supabase URL and server key.")
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        try:
            payload = key.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if claims["role"] != "service_role":
                raise ValueError()
        except Exception:
            raise BridgeError(
                "Use a Supabase server key, never a publishable or user token."
            ) from None
        headers["Authorization"] = "Bearer " + key
    return url, headers


def vault_call(profile, purpose, action="read", extra=None, environ=None):
    if profile not in ("production", "preview"):
        raise BridgeError("Select production or preview explicitly.")
    if purpose not in ("deployment", "runtime") or action not in ("read", "save", "delete"):
        raise BridgeError("Invalid Vault operation.")
    url, headers = supabase_connection(environ)
    request = urllib.request.Request(
        url + "/rest/v1/rpc/auto3d_modal_connection_" + action,
        data=json.dumps({"p_profile": profile, "p_purpose": purpose, **(extra or {})}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            body = response.read(16385)
        if len(body) > 16384:
            raise ValueError()
        record = json.loads(body)
    except BridgeError:
        raise
    except Exception:
        raise BridgeError("Could not access the Modal connection in Supabase Vault.") from None
    return record


def read_deployment(profile, environ=None):
    record = vault_call(profile, "deployment", environ=environ)
    try:
        if (
            record["schema"] != 1
            or record["profile"] != profile
            or record["purpose"] != "deployment"
        ):
            raise ValueError()
        settings = record["settings"]
        if (
            settings["app"] != "cloud-3d-studio"
            or settings["endpoint"] != ""
            or not re.fullmatch(r"ak-[A-Za-z0-9_-]{8,509}", settings["tokenId"])
            or not re.fullmatch(r"as-[A-Za-z0-9_-]{8,509}", settings["tokenSecret"])
            or not all(
                re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", settings[field])
                for field in ("workspace", "environment")
            )
        ):
            raise ValueError()
    except Exception:
        raise BridgeError(
            "Save a complete deployment token pair and target in Auto3D first."
        ) from None
    return settings


async def verify_target(settings):
    # Modal 1.6.4's own `modal token info` uses these RPCs. Kept here (never in Pages)
    # because API deployment tokens are not Web Function proxy credentials.
    from modal._environments import _list_environments
    from modal.client import _Client
    from modal.config import config
    from modal_proto import api_pb2

    if not all(
        server in {"https://api.modal.com", "https://api.modal2.com"}
        for server in config["server_url"].split(",")
    ):
        raise BridgeError("Use Modal's official API server for deployment credentials.")
    try:
        async with asyncio.timeout(30):
            async with await _Client.from_credentials(
                settings["tokenId"], settings["tokenSecret"]
            ) as client:
                info = await client._stub.TokenInfoGet(api_pb2.TokenInfoGetRequest())
                if info.workspace_name != settings["workspace"]:
                    raise BridgeError(
                        "The deployment token belongs to a different Modal workspace."
                    )
                environments = await _list_environments(client)
                if settings["environment"] not in [item.name for item in environments]:
                    raise BridgeError("The saved Modal environment is unavailable to this token.")
    except BridgeError:
        raise
    except Exception:
        raise BridgeError(
            "Modal authentication or network verification failed; no app was deployed."
        ) from None


def deployment_environment(settings, environ=None):
    environ = os.environ if environ is None else environ
    # Remove alternate Modal auth sources and Supabase server credentials from the child.
    child = {
        key: value for key, value in environ.items() if not key.startswith(("MODAL_", "SUPABASE_"))
    }
    child.update(
        MODAL_TOKEN_ID=settings["tokenId"],
        MODAL_TOKEN_SECRET=settings["tokenSecret"],
        MODAL_ENVIRONMENT=settings["environment"],
        CLOUD3D_WORKSPACE=settings["workspace"],
        CLOUD3D_ENVIRONMENT=settings["environment"],
        MODAL_LOGLEVEL="WARNING",
    )
    return child


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("production", "preview"))
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Explicitly build/deploy; may incur Modal usage charges.",
    )
    args = parser.parse_args(argv)
    try:
        settings = read_deployment(args.profile)
        asyncio.run(verify_target(settings))
        if args.deploy:
            # Capture rather than stream provider logs; upstream failures may include headers.
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
                cwd=Path(__file__).resolve().parents[1],
                env=deployment_environment(settings),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=900,
                check=False,
            )
            if result.returncode:
                raise BridgeError("Modal deployment failed. Inspect restricted provider logs.")
            print(
                "Cloud 3D Studio deployed to the verified target. Copy the gateway URL from Modal."
            )
        else:
            print(
                "Deployment token accepted; workspace and environment match. No app was deployed."
            )
        return 0
    except BridgeError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print(
            "The Vault runner could not complete; provider details were withheld.", file=sys.stderr
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
