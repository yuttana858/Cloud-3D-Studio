"""Credential-free proof worker. Only fixed check results may leave this module."""

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from cloud_3d_studio import HTTP_USER_AGENT

ORIGIN = "https://auto3d-5av.pages.dev"
ENGINE = "d5d9a1b9948c575fd46b22840971360c67878362"
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}
CODES = {
    "mesh_cancelled",
    "mesh_face_failure",
    "mesh_coverage_failed",
    "mesh_empty",
    "mesh_budget_exceeded",
    "source_units_required",
    "source_unavailable",
    "source_size_invalid",
    "source_read_failed",
    "source_incomplete",
    "lower_failed",
    "lower_incomplete",
    "mesh_incomplete",
    "export_failed",
    "reader_panicked",
    "mesh_invalid",
    "source_reader_disagreement",
    "proof_timeout",
    "proof_failed",
    "mesh_only_candidate",
    "reference_validated",
    "independent_validation_required",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect refused")


def summary(code, **checks):
    # No paths, dimensions, names, counts, source identifiers, text or geometry.
    return {
        "schemaVersion": 1,
        "engine": "cadconvert",
        "engineCommit": ENGINE,
        "code": code if code in CODES else "proof_failed",
        "checks": {
            name: checks.get(name, "not_run")
            if checks.get(name, "not_run") in {"passed", "failed", "not_run"}
            else "not_run"
            for name in ("sourceIntegrity", "sourceCoverage", "meshClosed", "dimensions")
        },
        "productionFallbackEnabled": False,
    }


def reference(value):
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "jobId",
        "token",
        "expiresAt",
        "bytes",
        "sha256",
        "sourceUnit",
    }:
        raise ValueError()
    if (
        value["schemaVersion"] != 1
        or not re.fullmatch(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", value["jobId"])
        or not re.fullmatch(r"[0-9]{10}\.[a-f0-9]{64}", value["token"])
    ):
        raise ValueError()
    if type(value["bytes"]) is not int or not 0 < value["bytes"] <= 50 * 1024 * 1024:
        raise ValueError()
    if not re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) or value["sourceUnit"] not in UNITS:
        raise ValueError()
    return value


def fetch_source(ref, path):
    ref = reference(ref)
    request = urllib.request.Request(
        ORIGIN + "/internal/conversion-proof/" + ref["jobId"] + "/source",
        headers={"Authorization": "Bearer " + ref["token"], "User-Agent": HTTP_USER_AGENT},
    )
    digest, total = hashlib.sha256(), 0
    with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
        if response.status != 200 or response.headers.get("Content-Length") != str(ref["bytes"]):
            raise ValueError()
        with path.open("wb") as stream:
            while block := response.read(65536):
                total += len(block)
                if total > ref["bytes"]:
                    raise ValueError()
                digest.update(block)
                stream.write(block)
    if total != ref["bytes"] or digest.hexdigest() != ref["sha256"]:
        raise ValueError()


def evaluate(source, unit, binary, directory, *, calibration=False):
    import numpy as np
    import trimesh
    from parasolid_kit import ParseLimits, read_brep

    checks = {"sourceIntegrity": "passed"}
    output, audit_path = directory / "result.glb", directory / "audit.json"
    child_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RAYON_NUM_THREADS": "2"}
    try:
        result = subprocess.run(
            [str(binary), str(source), unit, str(output), str(audit_path)],
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return summary("proof_timeout", **checks)
    if not audit_path.is_file() or audit_path.stat().st_size > 4 * 1024 * 1024:
        return summary("proof_failed", **checks)
    audit = json.loads(audit_path.read_bytes())
    if result.returncode or audit.get("status") != "meshed":
        checks["sourceCoverage"] = "failed"
        return summary(audit.get("code"), **checks)
    # A second reader must see exactly the same source faces; partial recovery fails.
    model = read_brep(
        source, limits=ParseLimits(max_file_size=50 * 1024 * 1024, max_diagnostics=1000)
    ).brep
    if (
        not model.complete
        or set(audit["sourceFaceIds"]) != {f.source.node_index for f in model.faces}
        or len(model.bodies) != audit["sourceBodies"]
    ):
        return summary("source_reader_disagreement", sourceCoverage="failed", **checks)
    checks["sourceCoverage"] = "passed"
    if not 0 < output.stat().st_size <= 64 * 1024 * 1024:
        return summary("mesh_invalid", **checks)
    scene = trimesh.load(output, force="scene", process=False, merge_primitives=True)
    # Bake GLB's metre/axis transform before checking. Weld only coincident seams
    # at 1e-8 m (0.00001 mm), far below the 0.1 mm tessellation tolerance.
    meshes = list(scene.dump())
    for mesh in meshes:
        mesh.merge_vertices(digits_vertex=8)
        if (
            not len(mesh.faces)
            or not np.isfinite(mesh.vertices).all()
            or not mesh.is_watertight
            or not mesh.is_winding_consistent
            or not math.isfinite(mesh.volume)
            or mesh.volume <= 0
        ):
            return summary("mesh_invalid", meshClosed="failed", **checks)
    if not meshes:
        return summary("mesh_invalid", meshClosed="failed", **checks)
    checks["meshClosed"] = "passed"
    # Complete CAD reconstruction is still required for an independent physical
    # reference. If OCCT cannot build it, this remains an unapproved mesh candidate.
    try:
        from parasolid_kit.interop.occt import to_occt

        from cloud_3d_studio.native import shape_metrics

        native = to_occt(
            model,
            source_unit=unit,
            target_unit="mm",
            require_complete=True,
            partial=False,
            heal=False,
            place_instances=True,
        )
        metrics = shape_metrics(native.shape)
        # GLB is Y-up: compare against the CAD Z-up axes, in mm.
        dimensions = scene.extents[[0, 2, 1]] * 1000
        tolerance = 0.25
        if not np.allclose(dimensions, metrics["dimensions_mm"], atol=tolerance, rtol=1e-4):
            return summary("mesh_invalid", dimensions="failed", **checks)
        if not math.isclose(
            sum(m.volume for m in meshes) * 1e9, metrics["volume_mm3"], rel_tol=0.01, abs_tol=1e-3
        ):
            return summary("mesh_invalid", dimensions="failed", **checks)
        checks["dimensions"] = "passed"
    except Exception:
        checks["dimensions"] = "not_run"
    if calibration and checks["dimensions"] != "passed":
        return summary("mesh_invalid", **checks)
    return summary(
        "reference_validated" if checks["dimensions"] == "passed" else "mesh_only_candidate",
        **checks,
    )


def prove(ref=None, *, source=None, unit="m", binary="/opt/cadconvert/proof", calibration=False):
    try:
        with tempfile.TemporaryDirectory(prefix="cad-proof-") as temporary:
            directory = Path(temporary)
            if ref is not None:
                source = directory / "source.x_t"
                fetch_source(ref, source)
                unit = ref["sourceUnit"]
            return evaluate(Path(source), unit, Path(binary), directory, calibration=calibration)
    except Exception:
        return summary("proof_failed")


if __name__ == "__main__":
    import sys

    try:
        data = json.loads(sys.stdin.buffer.read(8193))
        value = (
            prove(data["reference"])
            if data["reference"]
            else prove(source="/opt/cadconvert/public.x_t", calibration=True)
        )
    except Exception:
        value = summary("proof_failed")
    Path(sys.argv[1]).write_text(json.dumps(value))
