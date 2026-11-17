"""Local bounded-process boundary shared with the first private Modal smoke job."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .contracts import ConversionError, Limits, validate_options


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def frozen_input(source: Path, directory: Path, maximum: int) -> Path:
    if not source.is_file():
        raise ConversionError("missing_input", "Select an existing local CAD file.")
    target, total = directory / ("input" + source.suffix.lower()), 0
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        while chunk := incoming.read(min(1024 * 1024, maximum + 1 - total)):
            total += len(chunk)
            if total > maximum:
                raise ConversionError("input_limit", "Source exceeds the job byte limit.")
            outgoing.write(chunk)
    if not total:
        raise ConversionError("empty_input", "Source file is empty.")
    return target


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_process(arguments: list[str], directory: Path, timeout: float, cancel_check=None) -> dict:
    child_environment = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "TZ", "VIRTUAL_ENV"}
    }
    if "PYTHONPATH" in child_environment:
        child_environment["PYTHONPATH"] = os.pathsep.join(
            str(Path(path or ".").resolve())
            for path in child_environment["PYTHONPATH"].split(os.pathsep)
        )
    with (directory / "native.log").open("wb") as log:
        process = subprocess.Popen(
            arguments,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=directory,
            env={
                **child_environment,
                "OPENBLAS_NUM_THREADS": "2",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
            },
        )
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel_check and cancel_check():
                    raise ConversionError("cancelled", "The job was cancelled.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    process.wait(timeout=min(0.25, remaining))
                except subprocess.TimeoutExpired:
                    pass
        except subprocess.TimeoutExpired:
            stop_group(process)
            process.wait()
            raise ConversionError(
                "timeout", "Native conversion exceeded the job deadline."
            ) from None
        finally:
            stop_group(process)
            process.wait(timeout=5)
    result_path = directory / "result.json"
    if not result_path.is_file() or result_path.stat().st_size > 16 * 1024 * 1024:
        raise ConversionError("worker_failed", "Native worker failed without a bounded result.")
    try:
        result = json.loads(result_path.read_text())
    except (ValueError, UnicodeError):
        raise ConversionError(
            "worker_failed", "Native worker returned an invalid result."
        ) from None
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ConversionError("worker_failed", "Native worker returned an invalid result.")
    if not result["ok"] and (
        not isinstance(result.get("error"), dict)
        or not isinstance(result["error"].get("code"), str)
        or not isinstance(result["error"].get("message"), str)
    ):
        raise ConversionError("worker_failed", "Native worker returned an invalid failure.")
    if process.returncode and result["ok"]:
        raise ConversionError("worker_failed", "Native worker failed after preparing its result.")
    return result


def publish(staged: Path, destination: Path):
    # Exclusive directory creation preserves even a racing empty destination.
    destination.mkdir(mode=0o700)
    created = []
    try:
        for name in ("model.step", "model.glb", "model.stl", "manifest.json"):
            target = destination / name
            os.link(staged / name, target)
            created.append(target)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        try:
            destination.rmdir()
        except OSError:
            pass
        raise


def convert_file(
    source: Path | None,
    output: Path,
    source_unit: str | None = None,
    quality: str = "standard",
    limits: Limits | None = None,
    *,
    _smoke=False,
    cancel_check=None,
) -> dict:
    if sys.platform != "linux":
        raise ConversionError("linux_required", "Use Linux, WSL or the Modal worker.")
    limits = limits or Limits()
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ConversionError(
            "output_exists", "Choose a new output directory; existing files are preserved."
        )
    if not output.parent.is_dir():
        raise ConversionError("output_parent_missing", "Create the output parent directory first.")
    if not _smoke:
        if source is None:
            raise ConversionError("missing_input", "Select a CAD file.")
        validate_options(source.suffix, source_unit, quality)
    with tempfile.TemporaryDirectory(prefix=".cloud3d-", dir=output.parent) as temporary:
        directory = Path(temporary)
        frozen = None if _smoke else frozen_input(source, directory, limits.input_bytes)
        request = {
            "command": "self_test" if _smoke else "convert",
            "source": None if frozen is None else str(frozen),
            "source_unit": source_unit,
            "quality": quality,
            "limits": asdict(limits),
        }
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request))
        result = run_process(
            [sys.executable, "-m", "cloud_3d_studio.worker", str(request_path)],
            directory,
            limits.timeout_seconds,
            cancel_check=cancel_check,
        )
        if not result["ok"]:
            error = result["error"]
            raise ConversionError(error["code"], error["message"])
        manifest = result.get("manifest")
        if not (
            isinstance(manifest, dict)
            and manifest.get("status") == "validated"
            and isinstance(manifest.get("cad_validation"), dict)
            and manifest["cad_validation"].get("passed") is True
            and isinstance(manifest.get("mesh_validation"), dict)
            and manifest["mesh_validation"].get("passed") is True
            and isinstance(manifest.get("source"), dict)
            and isinstance(manifest.get("artifacts"), dict)
            and set(manifest["artifacts"]) == {"step", "glb", "stl"}
        ):
            raise ConversionError("validation_failed", "Native conversion evidence is incomplete.")
        if frozen is not None and manifest["source"].get("sha256") != digest(frozen):
            raise ConversionError(
                "source_hash_mismatch", "Source identity changed during conversion."
            )
        staged = directory / "artifacts"
        for kind, artifact in manifest["artifacts"].items():
            if not isinstance(artifact, dict) or artifact.get("filename") != "model." + kind:
                raise ConversionError(
                    "validation_failed", "Native worker returned an invalid artifact."
                )
            path = staged / artifact["filename"]
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != artifact.get("bytes")
                or not 0 < path.stat().st_size <= limits.output_bytes
                or artifact.get("units") != ("m" if kind == "glb" else "mm")
                or digest(path) != artifact.get("sha256")
            ):
                raise ConversionError(
                    "artifact_hash_mismatch", "Artifact identity verification failed."
                )
        manifest_file = staged / "manifest.json"
        if (
            manifest_file.is_symlink()
            or not manifest_file.is_file()
            or manifest_file.stat().st_size > 16 * 1024 * 1024
            or json.loads(manifest_file.read_text()) != manifest
        ):
            raise ConversionError(
                "validation_failed", "Published manifest differs from validation evidence."
            )
        try:
            publish(staged, output)
        except FileExistsError:
            raise ConversionError(
                "output_exists", "An output directory appeared; no existing files were replaced."
            ) from None
        return {"ok": True, "output_directory": str(output), "manifest": manifest}


def self_test() -> dict:
    with tempfile.TemporaryDirectory(prefix="cloud3d-smoke-") as temporary:
        result = convert_file(None, Path(temporary) / "result", _smoke=True)
        manifest = result["manifest"]
        if any(
            abs(a - b) > 1e-5
            for a, b in zip(manifest["cad"]["dimensions_mm"], (40, 30, 20), strict=True)
        ):
            raise ConversionError("self_test_failed", "Synthetic box dimensions changed.")
        return {
            "ok": True,
            "fixture": "synthetic STEP box",
            "version": manifest["producer_version"],
            "dimensions_mm": manifest["cad"]["dimensions_mm"],
            "topology": manifest["cad"]["topology"],
            "step_reimport_passed": manifest["cad_validation"]["passed"],
            "mesh_reimport_passed": manifest["mesh_validation"]["passed"],
            "artifacts": list(manifest["artifacts"]),
        }
