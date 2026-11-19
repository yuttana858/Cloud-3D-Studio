"""Thicken a product-side surface shell into a solid wall (molded pulp tool side).

Auto3D exports the product side of a formed pulp part as an open shell of B-spline and planar
faces. The tool side is that surface offset by the pulp thickness, where offsets of concave regions
collide; OCCT resolves those collisions (GeomAbs_Intersection joins). Import only inside a bounded
native process, like native.py.

Every result is checked independently of OCCT's own status: the solid must be valid, and sampled
points on every source face must have wall material at half the thickness on the chosen side and
none a quarter thickness on the product side.
"""

import math
from pathlib import Path

from .contracts import ConversionError

MAX_THICKNESS_MM = 20.0
SIDES = ("inside", "outside")
SEW_TOLERANCE_MM = 1e-3
OFFSET_TOLERANCE_MM = 1e-4
FIX_TOLERANCE_MM = 1e-2
SAMPLES_PER_FACE = 5


def _faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    explorer, faces = TopExp_Explorer(shape, TopAbs_FACE), []
    while explorer.More():
        faces.append(TopoDS.Face_s(explorer.Current()))
        explorer.Next()
    return faces


def _count(shape, kind):
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, mapped)
    return mapped.Extent()


def sew_shell(shape):
    """One connected shell from the source faces, or a named failure."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Sewing
    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID

    faces = _faces(shape)
    if not faces:
        raise ConversionError("thicken_no_faces", "The source has no surface faces to thicken.")
    if _count(shape, TopAbs_SOLID):
        raise ConversionError(
            "thicken_source_solid", "The source is already a solid; send the product-side surface."
        )
    # Work on a copy: OCCT's offset updates the faces it is given, and a second offset of the
    # same input faces crashed (segmentation fault) in testing.
    faces = _faces(BRepBuilderAPI_Copy(shape, True, False).Shape())
    sewing = BRepBuilderAPI_Sewing(SEW_TOLERANCE_MM)
    for face in faces:
        sewing.Add(face)
    sewing.Perform()
    sewn = sewing.SewedShape()
    if _count(sewn, TopAbs_SHELL) != 1 or sewn.ShapeType() != TopAbs_SHELL:
        raise ConversionError(
            "thicken_disconnected", "The product-side faces do not join into one connected surface."
        )
    return sewn, len(faces)


def _sample_points(face, count=SAMPLES_PER_FACE):
    """Interior points on a face with its outward normal (face orientation applied)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt2d, gp_Vec
    from OCP.TopAbs import TopAbs_IN

    surface, normals = BRepAdaptor_Surface(face), BRepGProp_Face(face)
    u0, u1, v0, v1 = (
        surface.FirstUParameter(),
        surface.LastUParameter(),
        surface.FirstVParameter(),
        surface.LastVParameter(),
    )
    points = []
    for a in range(count):
        for b in range(count):
            u = u0 + (u1 - u0) * (a + 0.5) / count
            v = v0 + (v1 - v0) * (b + 0.5) / count
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-7).State() != TopAbs_IN:
                continue
            point, normal = surface.Value(u, v), gp_Vec()
            normals.Normal(u, v, point, normal)  # BRepGProp_Face applies the face orientation.
            if normal.Magnitude() > 1e-9:
                points.append((point, normal.Normalized()))
    return points


def check_wall(solid, shell, thickness: float, sign: float) -> dict:
    """Wall material half a thickness along the offset side, none a quarter thickness behind, and
    no sample where the wall is thinner than requested (the first exit along the normal). Near a
    sharp edge the exit can be much farther than the thickness (a line along a steep wall), so only
    the minimum is meaningful.

    Each sample casts one line along its own normal through the solid and classifies points on it
    by crossing parity. OCCT's general point classifier shoots rays in arbitrary directions and
    misreports points near large B-spline faces; a line crossing the wall along its normal does not."""
    from OCP.gp import gp_Dir, gp_Lin
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(solid, 1e-7)
    checked, missing, extra, unclear, measured = 0, 0, 0, 0, []
    for face in _faces(shell):
        for point, normal in _sample_points(face):
            checked += 1
            direction = gp_Dir(normal.Multiplied(sign))
            intersector.Perform(gp_Lin(point, direction), -1e9, 1e9)
            hits = sorted(intersector.WParameter(i) for i in range(1, intersector.NbPnt() + 1))
            # One crossing through a shared edge reports a hit on each face: count it once.
            hits = [w for k, w in enumerate(hits) if k == 0 or w - hits[k - 1] > 1e-6]

            # Crossings counted towards both ends of the line must agree on a closed solid; a line
            # grazing a far face can drop one, which makes the sample unclear rather than wrong.
            def inside(depth, hits=hits):
                ahead, behind = sum(w > depth for w in hits) % 2, sum(w < depth for w in hits) % 2
                return None if ahead != behind else ahead == 1

            states = (inside(0.5 * thickness), inside(-0.25 * thickness))
            if None in states:
                unclear += 1
                continue
            if not states[0]:
                missing += 1
            if states[1]:
                extra += 1
            beyond = [w for w in hits if w > 0.05 * thickness]
            if beyond:
                measured.append(beyond[0])
    if not checked:
        raise ConversionError("thicken_unchecked", "No surface samples could be checked.")
    return {
        "samples": checked,
        "missing": missing,
        "extra": extra,
        "unclear": unclear,
        "min_thickness_mm": min(measured) if measured else None,
    }


def mesh_volume(solid, deflection: float) -> float:
    """Volume from a fine triangulation of a copy of the solid.

    BRepGProp's volume integral is off by a constant on closed (seamed) B-spline faces and their
    offsets: about 7,000 mm³ low on a six-pocket tray at every thickness. The mesh volume agrees
    with area x thickness."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location

    copy = BRepBuilderAPI_Copy(solid, True, False).Shape()
    BRepMesh_IncrementalMesh(copy, deflection, False, 0.1, True)
    volume = 0.0
    for face in _faces(copy):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise ConversionError("thicken_unchecked", "The thickened wall could not be measured.")
        transform, reversed_ = location.Transformation(), face.Orientation() == TopAbs_REVERSED
        for k in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(k).Get()
            if reversed_:
                b, c = c, b
            p, q, r = (triangulation.Node(i).Transformed(transform) for i in (a, b, c))
            volume += (
                p.X() * (q.Y() * r.Z() - r.Y() * q.Z())
                - q.X() * (p.Y() * r.Z() - r.Y() * p.Z())
                + r.X() * (p.Y() * q.Z() - q.Y() * p.Z())
            ) / 6
    return abs(volume)


def thicken_shape(shape, thickness: float, side: str = "inside"):
    """Solid wall from the product-side surface; side is where material goes vs. the normals."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.GProp import GProp_GProps
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.TopAbs import TopAbs_SOLID

    if not (isinstance(thickness, (int, float)) and math.isfinite(thickness)):
        raise ConversionError("thicken_invalid_thickness", "Thickness must be a finite number.")
    if not 0 < thickness <= MAX_THICKNESS_MM:
        raise ConversionError(
            "thicken_invalid_thickness",
            f"Thickness must be above 0 and at most {MAX_THICKNESS_MM} mm.",
        )
    if side not in SIDES:
        raise ConversionError("thicken_invalid_side", "Side must be inside or outside.")
    shell, face_count = sew_shell(shape)
    sign = -1.0 if side == "inside" else 1.0
    offset = BRepOffset_MakeOffset()
    # Shell, signed offset, tolerance, skin mode, no intersection or self-intersection pass,
    # intersection joins (collisions trimmed, no rolled arcs), thickening on, keep inner edges.
    offset.Initialize(
        shell,
        sign * thickness,
        OFFSET_TOLERANCE_MM,
        BRepOffset_Skin,
        False,
        False,
        GeomAbs_Intersection,
        True,
        False,
    )
    offset.MakeOffsetShape()
    if not offset.IsDone():
        raise ConversionError(
            "thicken_failed", f"OCCT could not offset the surface ({offset.Error().name})."
        )
    solid, repaired = offset.Shape(), False
    if not BRepCheck_Analyzer(solid).IsValid():
        fix = ShapeFix_Shape(solid)
        fix.SetPrecision(OFFSET_TOLERANCE_MM)
        fix.SetMaxTolerance(FIX_TOLERANCE_MM)
        fix.Perform()
        solid, repaired = fix.Shape(), True
    if (
        solid.IsNull()
        or not BRepCheck_Analyzer(solid).IsValid()
        or _count(solid, TopAbs_SOLID) != 1
    ):
        raise ConversionError("thicken_invalid", "The thickened wall is not one valid solid.")
    wall = check_wall(solid, shell, thickness, sign)
    # On a closed solid both crossing counts agree; many disagreements mean the wall is broken.
    if (
        wall["missing"]
        or wall["extra"]
        or wall["unclear"] > 0.05 * wall["samples"]
        or (wall["min_thickness_mm"] or 0) < 0.99 * thickness
    ):
        raise ConversionError(
            "thicken_wall_mismatch",
            "The thickened wall does not follow the surface everywhere; a fillet or corner is "
            "tighter "
            "than the thickness on the tool side.",
        )
    area = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shell, area, 1e-6)
    volume = mesh_volume(solid, min(0.05, thickness / 20))
    # A thin wall holds about area x thickness; edges and curvature move it by a few percent.
    if abs(volume - area.Mass() * thickness) > 0.1 * area.Mass() * thickness:
        raise ConversionError(
            "thicken_volume_mismatch", "The thickened wall's volume does not match its surface."
        )
    return solid, {
        "thickness_mm": thickness,
        "side": side,
        "source_faces": face_count,
        "surface_area_mm2": area.Mass(),
        "volume_mm3": volume,
        "shape_fix_applied": repaired,
        "wall_check": wall,
    }


def thicken_step(source: Path, output: Path, thickness: float, side: str = "inside") -> dict:
    """STEP surface in, validated STEP solid out (millimetres, independently reimported).

    Runs inside the bounded native worker; callers use jobs.thicken_file."""
    from .native import cold_check, compare_cad, read_step, shape_metrics, write_step

    shape, units = read_step(source)
    solid, report = thicken_shape(shape, thickness, side)
    metrics = shape_metrics(solid)
    write_step(solid, output)
    reimport = cold_check(output)
    compare_cad(metrics, reimport["metrics"])
    return {
        "source_units": units,
        "thicken": report,
        "cad": metrics,
        "cad_validation": {"passed": True, "process_isolated": True},
    }
