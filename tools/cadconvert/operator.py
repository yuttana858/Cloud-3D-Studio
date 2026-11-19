"""Trusted runner: issue one scoped reference; private CAD only travels R2 to Modal."""

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from cloud_3d_studio import HTTP_USER_AGENT
from deploy.provision_modal import control_key
from deploy.vault_modal import (
    NoRedirect,
    read_deployment,
    vault_call,
    verify_target,
)
from tools.cadconvert.worker import CODES, ENGINE, ORIGIN


def safe_result(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "engine",
            "engineCommit",
            "code",
            "checks",
            "productionFallbackEnabled",
        }
        or value["schemaVersion"] != 1
        or value["engine"] != "cadconvert"
        or value["engineCommit"] != ENGINE
        or value["code"] not in CODES
        or value["productionFallbackEnabled"] is not False
    ):
        raise ValueError()
    if set(value["checks"]) != {
        "sourceIntegrity",
        "sourceCoverage",
        "meshClosed",
        "dimensions",
    } or any(
        type(v) is not str or v not in {"passed", "failed", "not_run"}
        for v in value["checks"].values()
    ):
        raise ValueError()
    return value


def grant(job_id):
    runtime = vault_call("production", "runtime")["settings"]
    request = urllib.request.Request(
        ORIGIN + "/internal/conversion-control/proof",
        method="POST",
        data=json.dumps({"jobId": job_id}).encode(),
        headers={
            "Authorization": "Bearer " + control_key("production", runtime),
            "Content-Type": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        },
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        data = response.read(4097)
    if len(data) > 4096:
        raise ValueError()
    return json.loads(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--github-event", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        print("Proof not requested; no credentials read or server work started.")
        return 0
    if args.job_id and not re.fullmatch(
        r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", args.job_id
    ):
        print("Invalid job reference; no work started.", file=sys.stderr)
        return 1
    stage = "selection"
    try:
        job_id = args.job_id
        if args.github_event:
            if job_id or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
                raise ValueError()
            with Path(os.environ["GITHUB_EVENT_PATH"]).open("rb") as event_file:
                event_bytes = event_file.read(65537)
            if len(event_bytes) > 65536:
                raise ValueError()
            job_id = json.loads(event_bytes).get("inputs", {}).get("job_id", "")
            if (
                not isinstance(job_id, str)
                or job_id
                and not re.fullmatch(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", job_id)
            ):
                raise ValueError()
        stage = "vault_connection"
        settings = read_deployment("production")
        stage = "provider_authentication"
        asyncio.run(verify_target(settings))
        import modal

        from tools.cadconvert.modal_proof import app, private_proof

        stage = "image_startup"
        with modal.Client.from_credentials(settings["tokenId"], settings["tokenSecret"]) as client:
            with app.run(client=client, environment_name=settings["environment"]):
                stage = "public_reference"
                public = safe_result(private_proof.remote())
                print("Public reference: " + json.dumps(public, separators=(",", ":")), flush=True)
                if public["code"] != "reference_validated" or any(
                    v != "passed" for v in public["checks"].values()
                ):
                    return 1
                if job_id:
                    stage = "source_capability"
                    ref = grant(job_id)
                    stage = "private_reader"
                    result = safe_result(private_proof.remote(ref))
                    print("Private proof: " + json.dumps(result, separators=(",", ":")), flush=True)
                else:
                    print("No private job selected. Public proof only.")
        return 0
    except Exception:
        print(
            "Proof failed at " + stage + "; private provider/response details withheld.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
