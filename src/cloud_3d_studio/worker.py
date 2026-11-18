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
        from OCP.BRep import BRep_Builder
        from OCP.BRepPrimAPI import (
            BRepPrimAPI_MakeBox,
            BRepPrimAPI_MakeCone,
            BRepPrimAPI_MakeSphere,
        )
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
        from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TOC_RGB
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS_Compound
        from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

        from .appearance import Appearance, write_step_document

        source = directory / "input.step"
        builder, assembly = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(assembly)
        doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        root = shapes.NewShape()
        for shape in (
            BRepPrimAPI_MakeBox(40, 30, 20).Shape(),
            BRepPrimAPI_MakeSphere(gp_Pnt(10, 10, 10), 5).Shape(),
            BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(25, 15, 3), gp_Dir(0, 0, 1)), 4, 0, 8).Shape(),
        ):
            builder.Add(assembly, shape)
            part = shapes.AddShape(shape, False)
            colors.SetColor(
                part,
                Quantity_ColorRGBA(Quantity_Color(0.2, 0.05, 0.4, Quantity_TOC_RGB), 1),
                XCAFDoc_ColorSurf,
            )
            shapes.AddComponent(root, part, TopLoc_Location())
            face = TopExp_Explorer(shape, TopAbs_FACE).Current()
            colors.SetColor(
                shapes.AddSubShape(part, face),
                Quantity_ColorRGBA(Quantity_Color(0.05, 0.3, 0.7, Quantity_TOC_RGB), 0.5),
                XCAFDoc_ColorSurf,
            )
        shapes.UpdateAssemblies()
        write_step_document(Appearance("step", doc), source)
    else:
        source = Path(request["source"])
    kind = validate_options(source.suffix, request["source_unit"], request["quality"])
    source_hash = digest(source)
    output = directory / "artifacts"
    output.mkdir()
    progress = ProgressFile(directory)
    appearances = {}
    shape, source_info, cad, cad_validation = load_source(
        source,
        kind,
        request["source_unit"],
        source_hash,
        output / "model.step",
        progress=progress,
        appearance_out=appearances,
    )
    appearance = appearances["value"]
    mesh_validation = write_meshes(
        shape,
        output,
        cad,
        QUALITIES[request["quality"]],
        limits,
        progress=progress,
        appearance=appearance,
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
        "appearance": appearance.report(),
        "dependencies": {
            name: version(name)
            for name in ("parasolid-kit", "cadquery-ocp-novtk", "numpy", "trimesh")
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    if sum(path.stat().st_size for path in output.iterdir()) > limits.total_output_bytes:
        raise ConversionError("output_limit", "Combined artifacts exceed the job byte limit.")
    return {"ok": True, "manifest": manifest}


def execute_thicken(request, directory):
    from .thicken import SIDES, thicken_step

    source = Path(request["source"])
    if source.suffix.lower().lstrip(".") not in ("step", "stp"):
        raise ConversionError("unsupported_format", "Thickening reads STEP surfaces only.")
    thickness, side = request.get("thickness_mm"), request.get("side")
    if isinstance(thickness, bool) or not isinstance(thickness, (int, float)) or side not in SIDES:
        raise ConversionError("thicken_invalid_thickness", "Thickness and side are required.")
    source_hash = digest(source)
    output = directory / "artifacts"
    output.mkdir()
    progress = ProgressFile(directory)
    progress("read_source")
    evidence = thicken_step(source, output / "model.step", float(thickness), side)
    path = output / "model.step"
    limits = Limits(**request["limits"])
    if not path.is_file() or not 0 < path.stat().st_size <= limits.output_bytes:
        raise ConversionError("output_limit", "The output is empty or exceeds the job byte limit.")
    manifest = {
        "schema_version": 1,
        "producer": "cloud-3d-studio",
        "producer_version": __version__,
        "operation": "thicken",
        "status": "validated",
        "source": {
            "format": "step",
            "sha256": source_hash,
            "declared_units": evidence["source_units"],
        },
        "artifacts": {
            "step": {
                "filename": path.name,
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "units": "mm",
            }
        },
        "thicken": evidence["thicken"],
        "cad": evidence["cad"],
        "cad_validation": evidence["cad_validation"],
        "dependencies": {name: version(name) for name in ("cadquery-ocp-novtk",)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return {"ok": True, "manifest": manifest}


def main():
    request_path = Path(sys.argv[1])
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    try:
        apply_limits(Limits(**request["limits"]))
        if request.get("command") == "diagnose":
            from .diagnostics import diagnose

            appearances = {}
            result = {
                "ok": True,
                "report": diagnose(request, directory, appearance_out=appearances),
            }
            if request.get("include_appearance") is True and "value" in appearances:
                result["appearance"] = appearances["value"].report(source_only=True)
        elif request.get("command") == "thicken":
            result = execute_thicken(request, directory)
        else:
            result = execute(request, directory)
    except Exception as error:
        from .failures import MESSAGES, failure_code, failure_context
        from .progress import read_progress

        code = failure_code(error)
        from .diagnostics import diagnostic_report

        result = {
            "ok": False,
            "error": {
                "code": code,
                "message": MESSAGES.get(
                    code, "Native conversion failed; private source details withheld."
                ),
                "progress": read_progress(directory),
                "diagnostic": failure_context(error),
                "report": diagnostic_report(error, read_progress(directory)),
            },
        }
    (directory / "result.json").write_text(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
