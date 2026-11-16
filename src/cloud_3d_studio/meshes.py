"""A single bounded tessellation, with standard GLB and unit-qualified STL exports."""

from pathlib import Path

import numpy as np
import trimesh

from .contracts import ConversionError


def body_shapes(shape):
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    # Exclusions prevent a solid's shells or a shell's faces being emitted twice.
    for kind, avoid, solid in (
        (TopAbs_SOLID, None, True),
        (TopAbs_SHELL, TopAbs_SOLID, False),
        (TopAbs_FACE, TopAbs_SHELL, False),
    ):
        explorer = (
            TopExp_Explorer(shape, kind) if avoid is None else TopExp_Explorer(shape, kind, avoid)
        )
        while explorer.More():
            yield explorer.Current(), solid
            explorer.Next()


def triangulate(shape, deflection: float, limits):
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    BRepTools.Clean_s(shape)
    mesher = BRepMesh_IncrementalMesh(shape, deflection, False, 0.2, True)
    if not mesher.IsDone():
        raise ConversionError("meshing_failed", "OCCT meshing did not finish.")
    meshes, evidence, total = [], [], 0
    for body, solid in body_shapes(shape):
        if len(meshes) >= limits.bodies:
            raise ConversionError("body_limit", "CAD body count exceeds the job limit.")
        vertices, faces = [], []
        explorer = TopExp_Explorer(body, TopAbs_FACE)
        while explorer.More():
            face = TopoDS.Face_s(explorer.Current())
            location = TopLoc_Location()
            triangles = BRep_Tool.Triangulation_s(face, location)
            if triangles is None or triangles.NbTriangles() < 1:
                raise ConversionError("meshing_failed", "A required CAD face has no triangulation.")
            total += triangles.NbTriangles()
            if total > limits.triangles:
                raise ConversionError("triangle_limit", "Mesh exceeds the job triangle limit.")
            offset = len(vertices)
            for index in range(1, triangles.NbNodes() + 1):
                point = triangles.Node(index).Transformed(location.Transformation())
                vertices.append((point.X(), point.Y(), point.Z()))
            for index in range(1, triangles.NbTriangles() + 1):
                corners = [offset + x - 1 for x in triangles.Triangle(index).Get()]
                if face.Orientation() == TopAbs_REVERSED:
                    corners[1], corners[2] = corners[2], corners[1]
                faces.append(corners)
            explorer.Next()
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        # Validate a welded copy; retain separate CAD face vertices for sharp-edge shading.
        checked = mesh.copy()
        checked.merge_vertices(digits_vertex=10)
        if (
            not len(mesh.faces)
            or not np.isfinite(mesh.vertices).all()
            or (mesh.area_faces <= 0).any()
        ):
            raise ConversionError(
                "mesh_validation_failed", "Mesh is empty, nonfinite or degenerate."
            )
        if not checked.is_winding_consistent or (solid and not checked.is_watertight):
            raise ConversionError(
                "mesh_validation_failed", "Solid mesh is open or has inconsistent winding."
            )
        evidence.append(
            {
                "body": len(meshes) + 1,
                "expected_solid": solid,
                "triangles": len(mesh.faces),
                "watertight": bool(checked.is_watertight),
                "winding_consistent": bool(checked.is_winding_consistent),
            }
        )
        meshes.append(mesh)
    if not meshes:
        raise ConversionError("meshing_failed", "CAD has no supported mesh bodies.")
    return meshes, evidence


def mesh_metrics(mesh):
    return {
        "bounds_mm": mesh.bounds.reshape(-1).tolist(),
        "area_mm2": float(mesh.area),
        "volume_mm3": float(mesh.volume),
        "triangles": len(mesh.faces),
    }


def check_mesh(expected: dict, actual: dict, deflection: float, all_solid: bool):
    if not np.isfinite(np.asarray(actual["bounds_mm"])).all():
        raise ConversionError("mesh_validation_failed", "Exported mesh has invalid bounds.")
    if not np.allclose(expected["bounds_mm"], actual["bounds_mm"], atol=deflection * 2, rtol=1e-7):
        raise ConversionError("mesh_validation_failed", "Exported mesh changed physical bounds.")
    if abs(expected["area_mm2"] - actual["area_mm2"]) > max(expected["area_mm2"] * 0.02, 1e-6):
        raise ConversionError("mesh_validation_failed", "Exported mesh changed surface area.")
    if all_solid and abs(expected["volume_mm3"] - actual["volume_mm3"]) > max(
        expected["area_mm2"] * deflection * 2, 1e-6
    ):
        raise ConversionError("mesh_validation_failed", "Exported mesh changed physical volume.")


def write_meshes(shape, destination: Path, cad: dict, deflection: float, limits):
    meshes, bodies = triangulate(shape, deflection, limits)
    combined = trimesh.util.concatenate(meshes)
    solid = all(body["expected_solid"] for body in bodies)
    check_mesh(cad, mesh_metrics(combined), deflection, solid)
    scene = trimesh.Scene()
    for index, mesh in enumerate(meshes):
        center = mesh.bounds.mean(axis=0)
        local = mesh.copy()
        local.vertices = (local.vertices - center) * 0.001
        transform = np.eye(4)
        transform[:3, 3] = center * 0.001
        scene.add_geometry(
            local, geom_name=f"body-{index + 1}", node_name=f"body-{index + 1}", transform=transform
        )
    glb, stl = destination / "model.glb", destination / "model.stl"
    glb.write_bytes(scene.export(file_type="glb", include_normals=True))
    stl.write_bytes(combined.export(file_type="stl"))
    for file in (glb, stl):
        if file.stat().st_size > limits.output_bytes:
            raise ConversionError("output_limit", "Mesh output exceeds the job byte limit.")
    reopened_glb = trimesh.load(glb, force="scene", process=False)
    if len(reopened_glb.geometry) != len(meshes):
        raise ConversionError("mesh_validation_failed", "GLB lost CAD bodies.")
    glb_mm = reopened_glb.to_mesh()
    glb_mm.apply_scale(1000.0)
    reopened_stl = trimesh.load(stl, force="mesh", process=False)
    reports = {"glb": mesh_metrics(glb_mm), "stl": mesh_metrics(reopened_stl)}
    for report in reports.values():
        if report["triangles"] != len(combined.faces):
            raise ConversionError("mesh_validation_failed", "Export lost mesh triangles.")
        check_mesh(cad, report, deflection, solid)
    return {
        "passed": True,
        "deflection_mm": deflection,
        "triangles": len(combined.faces),
        "bodies": bodies,
        "reopened": reports,
        "stl_assembly_policy": "combined positioned triangles; no Boolean union",
    }
