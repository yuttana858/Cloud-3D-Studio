"""Run one bounded controller pass; inspect only status/error codes, never CAD contents."""

import argparse
import asyncio
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from cloud_3d_studio import HTTP_USER_AGENT
from deploy.provision_modal import control_key, verify_controller
from deploy.vault_modal import (
    BridgeError,
    NoRedirect,
    read_deployment,
    supabase_connection,
    vault_call,
    verify_target,
)

ORIGIN = "https://auto3d-5av.pages.dev"


def request_control(key, path, *, method="GET"):
    request = urllib.request.Request(
        ORIGIN + "/internal/conversion-control/" + path,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
            data = response.read(4097)
        if len(data) > 4096:
            raise BridgeError("Controller response exceeded its limit.")
        return json.loads(data)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise BridgeError("Controller " + path + " returned HTTP " + str(status) + ".") from None


def probe_filters():
    url, headers = supabase_connection()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    filters = {
        "pending": "and(state.eq.queued,dispatch_state.eq.pending)",
        "deadline": "and(state.in.(queued,running,cancelling,awaiting_upload),deadline_at.lt."
        + now
        + ")",
        "lease": "and(state.in.(running,cancelling),lease_until.lt." + now + ")",
        "retention": "and(state.neq.deleted,expires_at.lt." + now + ")",
    }
    filters["combined"] = ",".join(filters.values())
    for label, expression in filters.items():
        request = urllib.request.Request(
            url + "/rest/v1/auto3d_conversion_jobs?select=id&or=(" + expression + ")&limit=1",
            headers={**headers, "User-Agent": HTTP_USER_AGENT},
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
                response.read(4097)
            print("Database filter " + label + ": HTTP 200.")
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                body = json.loads(error.read(4096))
                code = body.get("code", "")
                code = (
                    code
                    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", code)
                    else "withheld"
                )
            except Exception:
                code = "withheld"
            finally:
                error.close()
            print("Database filter " + label + ": HTTP " + str(status) + "; code " + code + ".")


def startup_labels(message):
    """Strict public module/file labels only; discard arbitrary log messages."""
    labels = set()
    for line in message.splitlines():
        match = re.search(r"ModuleNotFoundError: No module named '([A-Za-z0-9_.]+)'", line)
        if match and match[1].split(".")[0] in {
            "deploy",
            "cloud_3d_studio",
            "modal",
            "numpy",
            "OCP",
            "parasolid_kit",
        }:
            labels.add("missing module " + match[1])
        for name in ("requirements-worker.lock", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            if "FileNotFoundError" in line and name in line:
                labels.add("missing deployment file " + name)
        match = re.search(
            r'File "[^"\n]*/(modal_app|remote|_container_entrypoint)\.py", line ([0-9]{1,8})', line
        )
        if match:
            labels.add(match[1] + ":" + match[2])
        for category in (
            "ModuleNotFoundError",
            "FileNotFoundError",
            "ImportError",
            "AttributeError",
            "RuntimeError",
            "MemoryError",
            "OSError",
            "TypeError",
            "ValueError",
            "ExecutionError",
        ):
            if re.search(r"\b" + category + r":", line):
                labels.add(category)
        match = re.search(
            r"Function has ([0-9]{1,4}) dependencies but container got ([0-9]{1,4}) object ids",
            line,
        )
        if match:
            labels.add("dependency mismatch " + match[1] + "/" + match[2])
    return sorted(labels)[:20]


async def diagnose_controller(settings):
    from modal._functions import _Function
    from modal.client import _Client

    async with await _Client.from_credentials(
        settings["tokenId"], settings["tokenSecret"]
    ) as client:
        controller = _Function.from_name(
            settings["app"],
            "cad_reconcile",
            environment_name=settings["environment"],
            client=client,
        )
        stats = await controller.get_current_stats()
        print(
            "Controller counts: "
            + json.dumps(
                {
                    name: getattr(stats, name)
                    for name in ("backlog", "num_total_runners", "num_running_inputs")
                },
                sort_keys=True,
            )
        )
        labels = set()
        async for entry in controller.logs.tail(entries=100):
            labels.update(startup_labels(entry.message))
        print("Controller startup labels: " + json.dumps(sorted(labels)[:20]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicitly reconcile pending jobs and temporary-file cleanup once.",
    )
    if not parser.parse_args(argv).run:
        print("Controller pass was not requested; no jobs changed.")
        return 0
    try:
        runtime = vault_call("production", "runtime")
        if not runtime:
            raise BridgeError("Production runtime is not provisioned.")
        key = control_key("production", runtime["settings"])
        if request_control(key, "health").get("ready") is not True:
            raise BridgeError("The controller health is not ready.")
        result = request_control(key, "reconcile", method="POST")
        if result.get("accepted") is not True:
            raise BridgeError("The controller API refused its pass.")
        print("The controller API accepted one bounded reconciliation pass.")
        settings = read_deployment("production")
        asyncio.run(verify_target(settings))
        asyncio.run(asyncio.wait_for(diagnose_controller(settings), timeout=30))
        asyncio.run(verify_controller(settings))
        print("The deployed scheduled controller also accepted its pass.")
        return 0
    except BridgeError as error:
        print(str(error), file=sys.stderr)
        try:
            probe_filters()
        except Exception:
            print("Database filter diagnostics could not complete; details withheld.")
    except Exception as error:
        print(
            "Controller invocation failed ("
            + type(error).__name__
            + "); private details withheld.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
