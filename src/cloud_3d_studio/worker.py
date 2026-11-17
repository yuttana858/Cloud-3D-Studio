"""Isolated native worker. No network upload, account credentials or HTTP listener."""

import json
import math
import sys
from importlib.metadata import version
from pathlib import Path

from . import __version__
from .contracts import QUALITIES, ConversionError, Limits, validate_options
from .jobs import digest
from .progress import ProgressFile


def apply_limits(limits):
    import resource

    for resource_id, ceiling in (
        (resource.RLIMIT_AS, limits.memory_bytes),
        (resource.RLIMIT_CPU, math.ceil(limits.timeout_seconds * 2)),
        (resource.RLIMIT_FSIZE, limits.output_bytes),
    ):
        _, hard = resource.getrlimit(resource_id)
        value = ceiling if hard == resource.RLIM_INFINITY else min(hard, ceiling)
        resource.setrlimit(resource_id, (value, value))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute(request, directory):
    from .meshes import write_meshes
    from .native import load_source

    limits = Limits(**request["limits"])
    if request["command"] == "self_test":
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

        from .native import write_step

        source = directory / "input.step"
        write_step(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), source)
    else:
        source = Path(request["source"])
    kind = validate_options(source.suffix, request["source_unit"], request["quality"])
    source_hash = digest(source)
    output = directory / "artifacts"
    output.mkdir()
    progress = ProgressFile(directory)
    shape, source_info, cad, cad_validation = load_source(
        source, kind, request["source_unit"], source_hash, output / "model.step", progress=progress
    )
    mesh_validation = write_meshes(
        shape, output, cad, QUALITIES[request["quality"]], limits, progress=progress
    )
    artifacts = {}
    for name, unit in (("step", "mm"), ("glb", "m"), ("stl", "mm")):
        path = output / ("model." + name)
        if not path.is_file() or not 0 < path.stat().st_size <= limits.output_bytes:
            raise ConversionError(
                "output_limit", "An output is empty or exceeds the job byte limit."
            )
        artifacts[name] = {
            "filename": path.name,
            "sha256": digest(path),
            "bytes": path.stat().st_size,
            "units": unit,
        }
    manifest = {
        "schema_version": 1,
        "producer": "cloud-3d-studio",
        "producer_version": __version__,
        "status": "validated",
        "source": {"format": kind, "sha256": source_hash, **source_info},
        "artifacts": artifacts,
        "cad": cad,
        "cad_validation": cad_validation,
        "mesh_validation": mesh_validation,
        "dependencies": {
            name: version(name)
            for name in ("parasolid-kit", "cadquery-ocp-novtk", "numpy", "trimesh")
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    if sum(path.stat().st_size for path in output.iterdir()) > limits.total_output_bytes:
        raise ConversionError("output_limit", "Combined artifacts exceed the job byte limit.")
    return {"ok": True, "manifest": manifest}


def main():
    request_path = Path(sys.argv[1])
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    try:
        apply_limits(Limits(**request["limits"]))
        result = execute(request, directory)
    except Exception as error:
        from .failures import MESSAGES, failure_code, failure_context
        from .progress import read_progress

        code = failure_code(error)
        result = {
            "ok": False,
            "error": {
                "code": code,
                "message": MESSAGES.get(
                    code, "Native conversion failed; private source details withheld."
                ),
                "progress": read_progress(directory),
                "diagnostic": failure_context(error),
            },
        }
    (directory / "result.json").write_text(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
