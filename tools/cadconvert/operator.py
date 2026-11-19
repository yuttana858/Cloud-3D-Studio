"""Trusted runner: issue one scoped reference; private CAD only travels R2 to Modal."""

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cloud_3d_studio import HTTP_USER_AGENT
from deploy.provision_modal import control_key
from deploy.vault_modal import (
    NoRedirect,
    read_deployment,
    supabase_connection,
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


def selected_request(path):
    """Time-bounded one-file approval, without publishing any customer identifier."""
    approval = json.loads(Path(path).read_text())
    if (
        set(approval) != {"approvedAt", "expiresAt", "selection"}
        or approval["selection"] != "single_existing_retained_failed_x_t"
    ):
        raise ValueError()
    now = datetime.now(timezone.utc)
    approved = datetime.fromisoformat(approval["approvedAt"])
    expires = datetime.fromisoformat(approval["expiresAt"])
    if (expires - approved).total_seconds() > 10800 or expires <= approved or now < approved:
        raise ValueError()
    if now >= expires:
        return ""  # Later code pushes run the public reference only.
    url, headers = supabase_connection()
    request = urllib.request.Request(
        url
        + "/rest/v1/auto3d_conversion_jobs?select=id,created_at&source_extension=eq.x_t&state=eq.failed&expires_at=gt."
        + now.isoformat().replace("+00:00", "Z")
        + "&limit=2",
        headers=headers,
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        data = response.read(4097)
    if len(data) > 4096:
        raise ValueError()
    jobs = json.loads(data)
    if len(jobs) != 1 or datetime.fromisoformat(jobs[0]["created_at"]) > approved:
        raise ValueError()
    return jobs[0]["id"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--authorized-request", default="")
    args = parser.parse_args(argv)
    if not args.run:
        print("Proof not requested; no credentials read or server work started.")
        return 0
    if args.job_id and not re.fullmatch(
        r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", args.job_id
    ):
        print("Invalid job reference; no work started.", file=sys.stderr)
        return 1
    try:
        job_id = args.job_id
        if args.authorized_request:
            if job_id:
                raise ValueError()
            job_id = selected_request(args.authorized_request)
        settings = read_deployment("production")
        asyncio.run(verify_target(settings))
        import modal

        from tools.cadconvert.modal_proof import app, private_proof

        with modal.Client.from_credentials(settings["tokenId"], settings["tokenSecret"]) as client:
            with app.run(client=client, environment_name=settings["environment"]):
                public = safe_result(private_proof.remote())
                print("Public reference: " + json.dumps(public, separators=(",", ":")), flush=True)
                if public["code"] != "reference_validated" or any(
                    v != "passed" for v in public["checks"].values()
                ):
                    return 1
                if job_id:
                    result = safe_result(private_proof.remote(grant(job_id)))
                    print("Private proof: " + json.dumps(result, separators=(",", ":")), flush=True)
                else:
                    print("No private job selected. Public proof only.")
        return 0
    except Exception:
        print("Proof failed; private provider/response details withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
