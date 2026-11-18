"""Explicit public-fixture canary through the deployed Auto3D user API; no customer data."""

import argparse
import hashlib
import json
import math
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

from cloud_3d_studio import HTTP_USER_AGENT
from deploy.provision_modal import control_key, controller_ready
from deploy.vault_modal import BridgeError, NoRedirect, supabase_connection, vault_call

ORIGIN = "https://auto3d-5av.pages.dev"
SOURCE = Path(__file__).with_name("fixtures") / "calibration-box.step"


class CanaryError(Exception):
    """Only fixed messages may reach the runner log."""


class CanaryClient:
    def __init__(self):
        self.url, self.headers = supabase_connection()
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, url, method="GET", headers=None, body=None, limit=1024 * 1024):
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body).encode()
            if body is not None
            else None
        )
        request = urllib.request.Request(
            url,
            method=method,
            headers={**(headers or {}), "User-Agent": HTTP_USER_AGENT},
            data=data,
        )
        try:
            with self.opener.open(request, timeout=45) as response:
                data = response.read(limit + 1)
            if len(data) > limit:
                raise CanaryError("Canary response exceeded its byte limit.")
            return data
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise CanaryError("Canary request failed with HTTP " + str(status) + ".") from None
        except CanaryError:
            raise
        except Exception:
            raise CanaryError(
                "Canary transfer failed; credentials and provider details withheld."
            ) from None

    def supabase(self, path, method="GET", body=None):
        data = self.request(self.url + "/auth/v1/" + path, method, self.headers, body)
        return json.loads(data) if data else None

    def account(self):
        email = "cad-canary-" + str(uuid4()) + "@auto3d.invalid"
        password = "Aa9!" + secrets.token_urlsafe(40)
        user = self.supabase(
            "admin/users",
            "POST",
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "app_metadata": {"auto3d_conversion_canary": True},
            },
        )
        owner = str(UUID(user["id"]))
        try:
            session = self.supabase(
                "token?grant_type=password", "POST", {"email": email, "password": password}
            )
            if session["user"]["id"] != owner or not isinstance(session["access_token"], str):
                raise CanaryError("Canary login identity did not match.")
            return {"owner": owner, "token": session["access_token"]}
        except Exception:
            self.supabase("admin/users/" + owner, "DELETE")
            raise CanaryError(
                "Canary login failed and its temporary identity was removed."
            ) from None

    def api(self, account, path="", method="GET", body=None, binary=False):
        headers = {
            "Cookie": "__Host-auto3d-access=" + account["token"],
            "X-Auto3D-Account": account["owner"],
            "Origin": ORIGIN,
            "Content-Type": "application/octet-stream" if binary else "application/json",
        }
        data = self.request(
            ORIGIN + "/api/conversions" + path,
            method,
            headers,
            body,
            limit=64 * 1024 * 1024 if binary else 1024 * 1024,
        )
        return data if binary else json.loads(data)


def run_diagnostic_canary(client, primary, outsider, *, sleep=time.sleep, monotonic=time.monotonic):
    """An intentionally invalid public fixture, never an existing customer job."""
    source = b"Public synthetic diagnostic fixture: this is not a Parasolid document."
    checksum = hashlib.sha256(source).hexdigest()
    result = client.api(
        primary,
        method="POST",
        body={
            "requestKey": str(uuid4()),
            "sourceName": "public-canary-diagnostic.x_t",
            "extension": "x_t",
            "sourceBytes": len(source),
            "sourceSha256": checksum,
            "sourceUnit": "mm",
            "quality": "standard",
            "origin": {"windowId": str(uuid4()), "tabId": str(uuid4()), "revision": 0},
        },
    )
    job_id = str(UUID(result["job"]["id"]))

    def denied():
        try:
            client.api(outsider, "/" + job_id)
        except CanaryError as error:
            if "404" in str(error):
                return
        raise CanaryError("Diagnostic report was not isolated to its owner.")

    def wait_report():
        deadline = monotonic() + 240
        while True:
            job = client.api(primary, "/" + job_id)["job"]
            if job["state"] == "failed" and job.get("purpose", "conversion") == "conversion":
                report = job.get("diagnostics", {}).get("report", {})
                if (
                    report.get("outcome") != "error_detected"
                    or report.get("failureCode") != "invalid_parasolid"
                    or report.get("checks", {}).get("sourceRead") != "failed"
                    or job.get("artifacts")
                    or job.get("errorCode") != "invalid_parasolid"
                    or job.get("source", {}).get("sha256") != checksum
                ):
                    raise CanaryError(
                        "The diagnostic report did not preserve failed-source evidence."
                    )
                if set(report) != {
                    "schemaVersion",
                    "versions",
                    "outcome",
                    "phase",
                    "failureCode",
                    "readerCode",
                    "exception",
                    "frames",
                    "checks",
                }:
                    raise CanaryError("Diagnostic report contained unexpected fields.")
                return job
            if job["state"] in ("ready", "cancelled", "expired") or monotonic() > deadline:
                raise CanaryError("The diagnostic pass did not complete within its bound.")
            sleep(2)

    try:
        denied()
        client.api(primary, "/" + job_id + "/source", "PUT", source, True)
        job = client.api(primary, "/" + job_id)["job"]
        client.api(primary, "/" + job_id + "/submit", "POST", {"revision": job["revision"]})
        job = wait_report()
        # A second pass reuses the immutable server upload without another upload.
        queued = client.api(
            primary, "/" + job_id + "/diagnose", "POST", {"revision": job["revision"]}
        )
        if queued.get("job", {}).get("purpose") != "diagnostic":
            raise CanaryError("The server did not queue the diagnostic pass.")
        wait_report()
        denied()
        return True
    finally:
        client.api(primary, "/" + job_id, "DELETE")


def run_canary(client=None, *, sleep=time.sleep, monotonic=time.monotonic):
    client = client or CanaryClient()
    accounts, job_id, primary = [], None, None
    cleanup_failed = False
    try:
        print("Live calibration: creating temporary test identities.")
        primary = client.account()
        accounts.append(primary)
        outsider = client.account()
        accounts.append(outsider)
        source = SOURCE.read_bytes()
        checksum = hashlib.sha256(source).hexdigest()
        capabilities = client.api(primary, "/capabilities")
        if not capabilities.get("ready"):
            raise CanaryError("Auto3D CAD service is not ready.")
        result = client.api(
            primary,
            method="POST",
            body={
                "requestKey": str(uuid4()),
                "sourceName": "public-canary-40x30x20.step",
                "extension": "step",
                "sourceBytes": len(source),
                "sourceSha256": checksum,
                "sourceUnit": None,
                "quality": "standard",
                "origin": {"windowId": str(uuid4()), "tabId": str(uuid4()), "revision": 0},
            },
        )
        job_id = str(UUID(result["job"]["id"]))
        try:
            client.api(outsider, "/" + job_id)
        except CanaryError as error:
            if "404" not in str(error):
                raise
        else:
            raise CanaryError("Cross-account job isolation failed.")
        print("Live calibration: private source upload and native conversion.")
        client.api(primary, "/" + job_id + "/source", "PUT", source, True)
        job = client.api(primary, "/" + job_id)["job"]
        client.api(primary, "/" + job_id + "/submit", "POST", {"revision": job["revision"]})
        deadline = monotonic() + 240
        while True:
            job = client.api(primary, "/" + job_id)["job"]
            if job["state"] == "ready":
                break
            if job["state"] in ("failed", "expired", "cancelled") or monotonic() > deadline:
                raise CanaryError(
                    "The public calibration conversion did not reach ready within its deadline."
                )
            sleep(2)
        print("Live calibration: checking dimensions and all artifact hashes/units.")
        summary = job["summary"]
        if (
            capabilities.get("progressProtocol") == 1
            and summary.get("progressReported") is not True
        ):
            raise CanaryError("The live worker did not publish negotiated progress telemetry.")
        if (
            summary.get("stepReimportPassed") is not True
            or summary.get("meshReimportPassed") is not True
            or len(summary.get("dimensionsMm", [])) != 3
            or any(
                type(a) not in (int, float) or not math.isfinite(a) or abs(a - b) > 1e-5
                for a, b in zip(summary["dimensionsMm"], (40, 30, 20), strict=True)
            )
        ):
            raise CanaryError("The live calibration dimensions or independent checks failed.")
        files = {
            kind: client.api(primary, "/" + job_id + "/artifacts/" + kind, binary=True)
            for kind in ("manifest", "step", "glb", "stl")
        }
        manifest = json.loads(files["manifest"])
        if manifest["source"]["sha256"] != checksum:
            raise CanaryError("The live manifest lost source identity.")
        for kind, units in (("step", "mm"), ("glb", "m"), ("stl", "mm")):
            record = manifest["artifacts"][kind]
            if (
                record["bytes"] != len(files[kind])
                or record["sha256"] != hashlib.sha256(files[kind]).hexdigest()
                or record["units"] != units
            ):
                raise CanaryError("The live artifact hash, length or units failed.")
        diagnostics = capabilities.get("diagnosticProtocol") == 1
        if diagnostics:
            run_diagnostic_canary(client, primary, outsider, sleep=sleep, monotonic=monotonic)
        return {
            "passed": True,
            "progressTelemetry": capabilities.get("progressProtocol") == 1,
            "serverDiagnostics": diagnostics,
            "dimensionsMm": [40, 30, 20],
            "twoAccountIsolation": True,
            "artifacts": ["step", "glb", "stl", "manifest"],
        }
    finally:
        if job_id and primary:
            try:
                client.api(primary, "/" + job_id, "DELETE")
            except Exception:
                cleanup_failed = True
        for account in accounts:
            try:
                client.supabase("admin/users/" + account["owner"], "DELETE")
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise CanaryError(
                "Canary cleanup could not be confirmed; temporary records need administrator review."
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Create two temporary confirmed test identities, convert a public box and remove all test data.",
    )
    args = parser.parse_args(argv)
    if not args.run:
        print("Canary was not requested; no accounts or conversions created.")
        return 0
    try:
        runtime = vault_call("production", "runtime")
        if not runtime or not controller_ready(control_key("production", runtime["settings"])):
            print("Auto3D connector is not live yet; public-fixture canary is pending.")
            return 0
        result = run_canary()
        print(
            "Live Auto3D private conversion passed: two-account isolation, 40 x 30 x 20 mm, all artifact hashes/units. Temporary identities and files were removed."
        )
        print(
            "Live progress telemetry was negotiated and verified."
            if result["progressTelemetry"]
            else "This app version does not advertise native progress telemetry."
        )
        print(
            "Live server diagnostics passed: automatic failure report, private source reuse, no model publication and owner-only report access."
            if result["serverDiagnostics"]
            else "This app version does not advertise server diagnostics."
        )
        return 0
    except (CanaryError, BridgeError) as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Live canary failed; sensitive response details were withheld.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
