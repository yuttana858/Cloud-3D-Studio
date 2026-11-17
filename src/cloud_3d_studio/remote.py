"""Owned-job transport: expiring job capability only, never a bucket or account key."""

import hashlib
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from . import HTTP_USER_AGENT
from .contracts import ConversionError, Limits, validate_options
from .jobs import convert_file, digest
from .progress import progress_value

AUTO3D_ORIGINS = ("https://auto3d-5av.pages.dev",)
ARTIFACTS = {
    "step": "model.step",
    "glb": "model.glb",
    "stl": "model.stl",
    "manifest": "manifest.json",
}


def reconcile_control(key):
    """Install once; poll readiness before the bounded private reconciliation call."""
    try:
        opener = urllib.request.build_opener(NoRedirect)
        base = AUTO3D_ORIGINS[0] + "/internal/conversion-control/"
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        }
        with opener.open(
            urllib.request.Request(base + "health", headers=headers), timeout=10
        ) as response:
            body = response.read(4097)
        if len(body) > 4096 or json.loads(body).get("ready") is not True:
            return {"accepted": False}
        with opener.open(
            urllib.request.Request(base + "reconcile", method="POST", data=b"{}", headers=headers),
            timeout=40,
        ) as response:
            return {"accepted": response.status == 200}
    except Exception:
        return {"accepted": False}


@dataclass(frozen=True, repr=False)
class JobReference:
    job_id: str
    attempt_id: str
    token: str
    origin: str

    @classmethod
    def parse(cls, value, allowed_origins=AUTO3D_ORIGINS):
        try:
            if not isinstance(value, dict) or set(value) != {
                "jobId",
                "attemptId",
                "token",
                "origin",
            }:
                raise ValueError()
            for key in ("jobId", "attemptId"):
                if str(UUID(value[key])) != value[key]:
                    raise ValueError()
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["token"])
                or value["origin"] not in allowed_origins
            ):
                raise ValueError()
        except Exception:
            raise ConversionError(
                "invalid_job_reference", "The job reference is invalid."
            ) from None
        return cls(value["jobId"], value["attemptId"], value["token"], value["origin"])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConversionError("transfer_failed", "Credentialed transfers cannot redirect.")


class JobTransport:
    def __init__(self, reference):
        self.reference = reference
        self.base = f"{reference.origin}/internal/conversion-worker/{reference.job_id}/{reference.attempt_id}/"
        self.opener = urllib.request.build_opener(NoRedirect)

    def open(self, operation, method="GET", data=None, headers=None):
        request = urllib.request.Request(
            self.base + operation,
            data=data,
            method=method,
            headers={
                **(headers or {}),
                "Authorization": "Bearer " + self.reference.token,
                "User-Agent": HTTP_USER_AGENT,
            },
        )
        try:
            return self.opener.open(request, timeout=45)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code in (401, 403, 404, 409, 410):
                raise ConversionError("cancelled", "The job is no longer active.") from None
            raise ConversionError("transfer_failed", "The private transfer failed.") from None
        except ConversionError:
            raise
        except Exception:
            raise ConversionError("transfer_failed", "The private transfer failed.") from None

    def json(self, operation, payload):
        with self.open(
            operation,
            "POST",
            json.dumps(payload, allow_nan=False).encode(),
            {"Content-Type": "application/json"},
        ) as response:
            content = response.read(16385)
        try:
            if len(content) > 16384:
                raise ValueError()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise ConversionError("protocol_error", "Invalid job-control response.") from None

    def download(self, path, expected_bytes, expected_hash, maximum, *, on_progress=None):
        total, checksum = 0, hashlib.sha256()
        with self.open("source") as incoming, path.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > expected_bytes or total > maximum:
                    raise ConversionError("input_limit", "Source exceeds the job byte limit.")
                checksum.update(chunk)
                outgoing.write(chunk)
                if on_progress:
                    on_progress(total, expected_bytes)
        if total != expected_bytes or checksum.hexdigest() != expected_hash:
            raise ConversionError("source_hash_mismatch", "Source verification failed.")

    def upload(self, kind, path):
        with path.open("rb") as stream:
            with self.open(
                "artifacts/" + kind,
                "PUT",
                stream,
                {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(path.stat().st_size),
                    "X-Content-SHA256": digest(path),
                },
            ) as response:
                if not 200 <= response.status < 300:
                    raise ConversionError("transfer_failed", "Artifact upload failed.")


def validate_claim(claim):
    try:
        source = claim["source"]
        extension = source["extension"]
        if extension not in ("x_t", "x_b", "step", "stp"):
            raise ValueError()
        validate_options(extension, source["unit"], claim["quality"])
        if not isinstance(source["bytes"], int) or not 0 < source["bytes"] <= Limits().input_bytes:
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            raise ValueError()
        return source
    except Exception:
        raise ConversionError("protocol_error", "Invalid source metadata.") from None


def run_owned_job(reference, *, transport_factory=JobTransport, converter=convert_file):
    transport = transport_factory(reference)
    stopped, cancelled = threading.Event(), threading.Event()
    monitor = None
    progress_protocol = False
    current = [{"stage": "downloading", "progress": progress_value("download_source")}]

    def update(value):
        value = progress_value(**value)
        phase = value["phase"]
        stage = (
            "downloading"
            if phase == "download_source"
            else "uploading"
            if phase in ("upload_artifacts", "finalizing")
            else "converting"
        )
        current[0] = {"stage": stage, "progress": value}

    def notify():
        snapshot = current[0]
        payload = snapshot if progress_protocol else {"stage": snapshot["stage"]}
        if transport.json("progress", payload).get("state") != "running":
            cancelled.set()

    try:
        claim = transport.json("claim", {})
        if claim.get("accepted") is not True:
            return {"accepted": False}
        progress_protocol = claim.get("progressProtocol") == 1
        source = validate_claim(claim)

        def progress():
            sent, at = None, 0
            while not stopped.wait(1):
                try:
                    snapshot = current[0]
                    if snapshot != sent or time.monotonic() - at >= 10:
                        notify()
                        sent, at = snapshot, time.monotonic()
                    if cancelled.is_set():
                        return
                except Exception:
                    # Fail closed if the account's lease can no longer be checked.
                    cancelled.set()
                    return

        monitor = threading.Thread(target=progress, daemon=True)
        monitor.start()
        with tempfile.TemporaryDirectory(prefix="auto3d-owned-") as temporary:
            root = Path(temporary)
            input_path = root / ("source." + source["extension"])
            transport.download(
                input_path,
                source["bytes"],
                source["sha256"],
                Limits().input_bytes,
                on_progress=lambda done, total: update(
                    progress_value("download_source", done, total, "bytes")
                ),
            )
            if cancelled.is_set():
                raise ConversionError("cancelled", "The job is no longer active.")
            update(progress_value("read_source"))
            notify()
            if cancelled.is_set():
                raise ConversionError("cancelled", "The job is no longer active.")
            output = root / "result"
            converter(
                input_path,
                output,
                source["unit"],
                claim["quality"],
                limits=Limits(output_bytes=64 * 1024 * 1024, total_output_bytes=208 * 1024 * 1024),
                cancel_check=cancelled.is_set,
                on_progress=update,
            )
            update(progress_value("upload_artifacts", 0, len(ARTIFACTS), "files"))
            notify()
            for count, (kind, filename) in enumerate(ARTIFACTS.items(), 1):
                if cancelled.is_set():
                    raise ConversionError("cancelled", "The job is no longer active.")
                transport.upload(kind, output / filename)
                update(progress_value("upload_artifacts", count, len(ARTIFACTS), "files"))
                notify()
            stopped.set()
            monitor.join(timeout=46)
            if cancelled.is_set():
                raise ConversionError("cancelled", "The job is no longer active.")
            update(progress_value("finalizing"))
            notify()
            transport.json("complete", {})
        return {"accepted": True, "completed": True}
    except Exception as error:
        # Codes only; native diagnostics/filenames/credentials are never provider results or logs.
        code = getattr(error, "code", "conversion_failed")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
            code = "conversion_failed"
        try:
            notify()
        except Exception:
            pass
        try:
            context = getattr(error, "context", None)
            transport.json(
                "failure",
                {
                    "code": code,
                    **({"diagnostic": context} if context and progress_protocol else {}),
                },
            )
        except Exception:
            pass
        return {"accepted": True, "completed": False, "code": code}
    finally:
        stopped.set()
        if monitor:
            monitor.join(timeout=46)
