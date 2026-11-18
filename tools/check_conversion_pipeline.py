"""Verify the existing deployment without rebuilding; log fixed operation labels only."""

import argparse
import re
import sys

from deploy.canary_modal import CanaryClient, CanaryError, run_canary
from deploy.provision_modal import control_key, controller_ready, proxy_probe
from deploy.vault_modal import vault_call


def api_operation(path):
    if not path:
        return "conversions"
    if path == "/capabilities":
        return "capabilities"
    parts = path.strip("/").split("/")
    if len(parts) == 1:
        return "job"
    if len(parts) == 2 and parts[1] in {"source", "submit", "cancel", "retry", "diagnose"}:
        return "job." + parts[1]
    if (
        len(parts) == 3
        and parts[1] == "artifacts"
        and parts[2] in {"step", "glb", "stl", "manifest"}
    ):
        return "artifact." + parts[2]
    return "other"


def failed_operation(operation, method, error):
    method = method if method in {"GET", "POST", "PUT", "DELETE"} else "OTHER"
    match = re.search(r"\bHTTP ([0-9]{3})\b", str(error))
    status = " HTTP " + match[1] if match else ""
    print("Pipeline " + method + " " + operation + " failed." + status, file=sys.stderr)


class TracedClient(CanaryClient):
    def api(self, account, path="", method="GET", body=None, binary=False):
        try:
            return super().api(account, path, method, body, binary)
        except Exception as error:
            failed_operation("api." + api_operation(path), method, error)
            raise

    def supabase(self, path, method="GET", body=None):
        operation = "auth.users" if path.startswith("admin/users") else "auth.signin"
        try:
            return super().supabase(path, method, body)
        except Exception as error:
            failed_operation(operation, method, error)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Run the public fixture and clean it up."
    )
    args = parser.parse_args(argv)
    if not args.run:
        print("Pipeline check not requested; no credentials read or jobs created.")
        return 0
    try:
        runtime = vault_call("production", "runtime")
        if not runtime:
            raise CanaryError("Runtime connection missing.")
        settings = runtime["settings"]
        print("Pipeline check: protected Modal gateway.", flush=True)
        proxy_probe(settings["endpoint"], settings, settings, require_ready=True)
        print("Pipeline check: Auto3D controller readiness.", flush=True)
        if not controller_ready(control_key("production", settings)):
            raise CanaryError("Controller is not ready.")
        result = run_canary(TracedClient())
        if not result["serverDiagnostics"] or not result["progressTelemetry"]:
            raise CanaryError("Required protocol was not verified.")
        print("Live pipeline passed: calibration, hashes/units, progress and server diagnostics.")
        print("Temporary test identities and files were removed.")
        return 0
    except Exception:
        print("Pipeline verification failed; private response details withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
