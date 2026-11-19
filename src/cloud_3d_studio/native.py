"""OCCT exchange and measurement. Import only inside a bounded native process."""

import json
import math
import subprocess
import sys
from pathlib import Path

from .contracts import ConversionError


def read_step(path: Path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ConversionError("invalid_step", "OCCT could not read this STEP file.")
    sequences = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*sequences)
    units = [sequences[0].Value(i).ToCString() for i in range(1, sequences[0].Length() + 1)]
    if not units:
        raise ConversionError(
            "source_units_required", "STEP has no supported declared length unit."
        )
    reader.SetSystemLengthUnit(1.0)  # OCCT millimeters, after ReadFile creates its model.
    roots = reader.NbRootsForTransfer()
    transferred = reader.TransferRoots()
    if roots < 1 or transferred != roots or reader.NbShapes() < 1:
        raise ConversionError("incomplete_step", "Not every STEP root transferred successfully.")
    return reader.OneShape(), units


def write_step(shape, path: Path, unit: str = "MM"):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    if not Interface_Static.SetCVal_s("write.step.schema", "AP242DIS"):
        raise ConversionError("step_export_failed", "AP242 export is unavailable.")
    if not Interface_Static.SetCVal_s("write.step.unit", unit):
        raise ConversionError("step_export_failed", "STEP output unit is unavailable.")
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise ConversionError("step_export_failed", "The complete shape could not be transferred.")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise ConversionError("step_export_failed", "STEP could not be written.")


def shape_metrics(shape) -> dict:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ConversionError("invalid_geometry", "The CAD shape is empty or invalid.")
    counts = {}
    for name, kind in (
        ("solids", TopAbs_SOLID),
        ("shells", TopAbs_SHELL),
        ("faces", TopAbs_FACE),
        ("edges", TopAbs_EDGE),
        ("vertices", TopAbs_VERTEX),
    ):
        mapped = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, mapped)
        counts[name] = mapped.Extent()
    if not counts["faces"]:
        raise ConversionError("unsupported_geometry", "This worker requires surface geometry.")
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid() or box.IsOpen():
        raise ConversionError("invalid_bounds", "CAD has no finite bounding box.")
    bounds = list(box.Get())
    surface, mass = GProp_GProps(), GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface)
    BRepGProp.VolumeProperties_s(shape, mass)
    if not all(math.isfinite(x) for x in bounds + [surface.Mass(), mass.Mass()]):
        raise ConversionError("invalid_geometry", "CAD contains nonfinite measurements.")
    return {
        "bounds_mm": bounds,
        "dimensions_mm": [bounds[i + 3] - bounds[i] for i in range(3)],
        "area_mm2": surface.Mass(),
        "volume_mm3": mass.Mass(),
        "topology": counts,
    }


def compare_cad(expected: dict, actual: dict, measure_rel_tol: float | None = 1e-7):
    """Topology and bounds must survive the STEP round trip; area and volume within measure_rel_tol.
    None skips area and volume: BRepGProp integrates offset surfaces too loosely to compare them
    (thickening compares mesh volumes instead)."""
    if expected["topology"] != actual["topology"]:
        raise ConversionError("step_validation_failed", "STEP round trip changed topology.")
    for name in (
        ("bounds_mm",) if measure_rel_tol is None else ("bounds_mm", "area_mm2", "volume_mm3")
    ):
        left = expected[name] if isinstance(expected[name], list) else [expected[name]]
        right = actual[name] if isinstance(actual[name], list) else [actual[name]]
        rel_tol = 1e-7 if name == "bounds_mm" else measure_rel_tol
        if not all(
            math.isclose(a, b, abs_tol=1e-5, rel_tol=rel_tol)
            for a, b in zip(left, right, strict=True)
        ):
            raise ConversionError("step_validation_failed", f"STEP round trip changed {name}.")


def cold_check(path: Path) -> dict:
    result = path.with_suffix(".reimport.json")
    completed = subprocess.run(
        [sys.executable, "-m", "cloud_3d_studio.native", str(path), str(result)],
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if completed.returncode or not result.is_file():
        raise ConversionError("step_validation_failed", "Independent STEP reimport failed.")
    report = json.loads(result.read_text())
    result.unlink()
    if report.get("declared_units") != ["millimetre"]:
        raise ConversionError("step_validation_failed", "STEP does not declare millimeters.")
    return report


def load_source(
    path: Path,
    kind: str,
    source_unit: str | None,
    source_hash: str,
    output: Path,
    *,
    progress=lambda *args: None,
    appearance_out=None,
):
    from .appearance import Appearance, read_step_document, write_step_document

    progress("read_source")
    appearance = Appearance(kind)
    if kind == "step":
        shape, units, appearance = read_step_document(path)
        source = {"declared_units": units, "complete": True}
    elif kind == "parasolid":
        from parasolid_kit import ParseLimits, read_brep
        from parasolid_kit.interop.limits import InteropLimits
        from parasolid_kit.interop.occt import to_occt

        parsed = read_brep(
            path, limits=ParseLimits(max_file_size=50 * 1024 * 1024, max_diagnostics=1000)
        )
        progress("build_geometry")
        converted = to_occt(
            parsed.brep,
            source_unit=source_unit,
            target_unit="mm",
            require_complete=True,
            partial=False,
            heal=False,
            place_instances=True,
            source_identity="sha256:" + source_hash,
            limits=InteropLimits(max_diagnostics=1000),
        )
        if not (
            converted.report.source_complete
            and converted.report.conversion_complete
            and converted.report.occt_valid
        ):
            raise ConversionError("incomplete_conversion", "Parasolid conversion is incomplete.")
        shape = converted.shape
        source = {
            "coordinate_unit": source_unit,
            "schema_key": parsed.brep.schema_key,
            "complete": True,
            "assembly_instances_placed": True,
            "body_count": len(parsed.brep.bodies),
            "instance_count": len(parsed.brep.instances),
        }
    else:
        raise ConversionError("unsupported_format", "No reader is enabled for this CAD format.")
    progress("validate_geometry")
    original = shape_metrics(shape)
    progress("write_step")
    if appearance.document is not None:
        write_step_document(appearance, output)
    else:
        write_step(shape, output)
        appearance.step_verified = True
    progress("validate_step")
    reimport = cold_check(output)
    compare_cad(original, reimport["metrics"])
    if appearance_out is not None:
        appearance_out["value"] = appearance
    return shape, source, original, {"passed": True, "process_isolated": True, **reimport}


if __name__ == "__main__":
    shape, units = read_step(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(
        json.dumps({"metrics": shape_metrics(shape), "declared_units": units}, allow_nan=False)
    )
