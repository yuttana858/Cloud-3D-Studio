"""Physical interchange, bounded execution and capability truthfulness."""

import json
import math
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import trimesh
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeSphere,
)
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopoDS import TopoDS_Compound

from cloud_3d_studio.contracts import ConversionError, Limits, capabilities, validate_options
from cloud_3d_studio.jobs import convert_file, digest, frozen_input, run_process, self_test
from cloud_3d_studio.meshes import triangulate
from cloud_3d_studio.native import load_source, shape_metrics, write_step


class ContractsTests(unittest.TestCase):
    def test_registry_separates_enabled_and_planned_formats(self):
        registry = capabilities()
        self.assertEqual(set(registry["inputs"]), {"parasolid", "step"})
        self.assertEqual(set(registry["outputs"]), {"step", "glb", "stl"})
        with self.assertRaises(ConversionError) as error:
            validate_options(".iges", "mm", "standard")
        self.assertEqual(error.exception.code, "unsupported_format")

    def test_units_are_explicit_for_parasolid_and_embedded_for_step(self):
        for unit in (None, "guess", "inch"):
            with self.assertRaises(ConversionError):
                validate_options(".x_t", unit, "standard")
        self.assertEqual(validate_options(".X_B", "in", "fine"), "parasolid")
        with self.assertRaises(ConversionError) as error:
            validate_options("step", "in", "standard")
        self.assertEqual(error.exception.code, "embedded_units")

    def test_unimplemented_adapter_cannot_fall_back_to_another_reader(self):
        with self.assertRaises(ConversionError) as error:
            load_source(Path("future.iges"), "iges", "mm", "0" * 64, Path("unused.step"))
        self.assertEqual(error.exception.code, "unsupported_format")

    def test_limits_reject_invalid_values(self):
        for kwargs in (
            {"input_bytes": 0},
            {"triangles": True},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": 901},
        ):
            with self.assertRaises(ConversionError):
                Limits(**kwargs)

    def test_input_copy_is_bounded_immutable_and_handles_empty_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "product.step"
            source.write_bytes(b"original")
            stage = root / "stage"
            stage.mkdir()
            frozen = frozen_input(source, stage, 8)
            source.write_bytes(b"changed")
            self.assertEqual(frozen.read_bytes(), b"original")
            frozen.unlink()
            with self.assertRaises(ConversionError):
                frozen_input(source, stage, 2)
            frozen.unlink()
            source.write_bytes(b"")
            with self.assertRaises(ConversionError) as error:
                frozen_input(source, stage, 8)
            self.assertEqual(error.exception.code, "empty_input")

    def test_process_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ConversionError) as error:
                run_process(
                    [sys.executable, "-c", "import time; time.sleep(5)"], Path(temporary), 0.05
                )
            self.assertEqual(error.exception.code, "timeout")

    def test_malformed_native_results_are_actionable(self):
        for payload in ("not JSON", '{"ok": false}', '{"ok": "yes"}'):
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                code = f"from pathlib import Path; Path({str(directory / 'result.json')!r}).write_text({payload!r})"
                with self.assertRaises(ConversionError) as error:
                    run_process([sys.executable, "-c", code], directory, 2)
                self.assertEqual(error.exception.code, "worker_failed")

    def test_missing_success_evidence_cannot_publish_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source.step", Path(temporary) / "output"
            source.write_bytes(b"synthetic placeholder")
            with patch(
                "cloud_3d_studio.jobs.run_process", return_value={"ok": True, "manifest": None}
            ):
                with self.assertRaises(ConversionError) as error:
                    convert_file(source, output)
            self.assertEqual(error.exception.code, "validation_failed")
            self.assertFalse(output.exists())


class MeshIntegrityTests(unittest.TestCase):
    def test_cleanup_does_not_hide_a_missing_face_or_nonfinite_coordinates(self):
        constructor = trimesh.Trimesh
        for corruption in ("missing_face", "nonfinite"):
            with self.subTest(corruption=corruption):

                def corrupt_mesh(*, vertices, faces, process):
                    points, triangles = np.array(vertices), np.array(faces)
                    if corruption == "nonfinite":
                        points[0, 0] = np.nan
                    else:
                        # Both triangles belonging to one required CAD face collapse.
                        triangles[:2] = triangles[:2, :1]
                    return constructor(vertices=points, faces=triangles, process=process)

                with patch("cloud_3d_studio.meshes.trimesh.Trimesh", side_effect=corrupt_mesh):
                    with self.assertRaises(ConversionError) as error:
                        triangulate(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), 0.1, Limits())
                self.assertEqual(error.exception.code, "mesh_validation_failed")


class NativeConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def convert_shape(self, shape, unit="MM", quality="standard", limits=None):
        source = self.root / "input.step"
        write_step(shape, source, unit)
        before = digest(source)
        output = self.root / "result"
        result = convert_file(source, output, quality=quality, limits=limits)
        self.assertEqual(digest(source), before)
        manifest = result["manifest"]
        self.assertEqual(manifest, json.loads((output / "manifest.json").read_text()))
        self.assertEqual(set(manifest["artifacts"]), {"step", "glb", "stl"})
        for key, artifact in manifest["artifacts"].items():
            path = output / artifact["filename"]
            self.assertEqual(digest(path), artifact["sha256"])
            self.assertEqual(path.stat().st_size, artifact["bytes"])
            self.assertEqual(artifact["units"], "m" if key == "glb" else "mm")
        self.assertTrue(manifest["cad_validation"]["process_isolated"])
        self.assertTrue(manifest["mesh_validation"]["passed"])
        return manifest, output

    def test_box_roundtrips_all_formats_and_preserves_input(self):
        manifest, output = self.convert_shape(BRepPrimAPI_MakeBox(40, 30, 20).Shape())
        np.testing.assert_allclose(manifest["cad"]["dimensions_mm"], [40, 30, 20], atol=1e-6)
        glb = trimesh.load(output / "model.glb", force="scene").to_mesh()
        stl = trimesh.load(output / "model.stl", force="mesh")
        np.testing.assert_allclose(glb.extents, [0.04, 0.03, 0.02], atol=1e-8)
        np.testing.assert_allclose(stl.extents, [40, 30, 20], atol=1e-6)
        self.assertTrue(stl.is_watertight)
        # A mechanical box retains six flat normal directions, rather than rounded corners.
        raw_scene = trimesh.load(output / "model.glb", force="scene", process=False)
        body = next(iter(raw_scene.geometry.values()))
        normals = np.unique(np.round(body.vertex_normals, 6), axis=0)
        self.assertEqual(normals.shape, (6, 3))

    def test_step_inches_are_normalized_using_the_file_unit(self):
        manifest, _ = self.convert_shape(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), unit="INCH")
        self.assertIn("inch", [value.lower() for value in manifest["source"]["declared_units"]])
        np.testing.assert_allclose(manifest["cad"]["dimensions_mm"], [40, 30, 20], atol=1e-5)

    def test_cylinder_is_closed_and_has_expected_volume(self):
        manifest, _ = self.convert_shape(BRepPrimAPI_MakeCylinder(10, 20).Shape())
        self.assertAlmostEqual(manifest["cad"]["volume_mm3"], math.pi * 100 * 20, places=4)
        self.assertTrue(manifest["mesh_validation"]["bodies"][0]["watertight"])

    def test_sphere_roundtrips_without_pole_degeneracies(self):
        manifest, output = self.convert_shape(BRepPrimAPI_MakeSphere(10).Shape())
        self.assertAlmostEqual(manifest["cad"]["volume_mm3"], 4 * math.pi * 1000 / 3, places=4)
        self.assertGreater(
            manifest["mesh_validation"]["bodies"][0]["zero_area_triangles_removed"], 0
        )
        mesh = trimesh.load(output / "model.stl", force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertTrue((mesh.area_faces > 0).all())

    def test_pointed_cone_roundtrips_without_pole_degeneracies(self):
        manifest, output = self.convert_shape(BRepPrimAPI_MakeCone(10, 0, 20).Shape())
        self.assertAlmostEqual(manifest["cad"]["volume_mm3"], math.pi * 100 * 20 / 3, places=4)
        self.assertGreater(
            manifest["mesh_validation"]["bodies"][0]["zero_area_triangles_removed"], 0
        )
        mesh = trimesh.load(output / "model.stl", force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertTrue((mesh.area_faces > 0).all())

    def test_fine_quality_increases_curve_sampling_and_keeps_dimensions(self):
        source = self.root / "curved.step"
        write_step(BRepPrimAPI_MakeCylinder(100, 20).Shape(), source)
        reports = [
            convert_file(source, self.root / quality, quality=quality)["manifest"]
            for quality in ("standard", "fine")
        ]
        self.assertGreater(
            reports[1]["mesh_validation"]["triangles"], reports[0]["mesh_validation"]["triangles"]
        )
        for report in reports:
            np.testing.assert_allclose(report["cad"]["dimensions_mm"], [200, 200, 20], atol=1e-5)

    def test_through_hole_is_preserved(self):
        box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
        cylinder = BRepPrimAPI_MakeCylinder(5, 20).Shape()
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(20, 15, 0))
        cylinder = BRepBuilderAPI_Transform(cylinder, transform, True).Shape()
        result = BRepAlgoAPI_Cut(box, cylinder)
        manifest, output = self.convert_shape(result.Shape())
        self.assertAlmostEqual(manifest["cad"]["volume_mm3"], 24000 - math.pi * 25 * 20, places=4)
        mesh = trimesh.load(output / "model.stl", force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertEqual(mesh.euler_number, 0)

    def test_rotated_translated_bodies_keep_world_placements(self):
        builder, compound = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(compound)
        box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
        shift, turn = gp_Trsf(), gp_Trsf()
        shift.SetTranslation(gp_Vec(100, 0, 0))
        turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 2)
        turn.SetTranslationPart(gp_Vec(0, 0, 50))
        for transform in (shift, turn):
            builder.Add(compound, BRepBuilderAPI_Transform(box, transform, True).Shape())
        manifest, output = self.convert_shape(compound)
        self.assertEqual(manifest["cad"]["topology"]["solids"], 2)
        np.testing.assert_allclose(
            manifest["cad"]["bounds_mm"], [-30, 0, 0, 140, 40, 70], atol=1e-5
        )
        scene = trimesh.load(output / "model.glb", force="scene")
        self.assertEqual(len(scene.geometry), 2)
        np.testing.assert_allclose(
            scene.bounds.reshape(-1) * 1000, [-30, 0, 0, 140, 40, 70], atol=1e-5
        )

    def test_limits_produce_no_published_partial_artifacts(self):
        source = self.root / "input.step"
        write_step(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), source)
        for key, limit in (
            ("input", Limits(input_bytes=20)),
            ("triangles", Limits(triangles=2)),
            ("total", Limits(total_output_bytes=100)),
        ):
            output = self.root / key
            with self.assertRaises(ConversionError):
                convert_file(source, output, limits=limit)
            self.assertFalse(output.exists())
            self.assertFalse(list(self.root.glob(".cloud3d-*")))

    def test_existing_output_is_preserved(self):
        output = self.root / "keep"
        output.mkdir()
        (output / "keep.txt").write_text("unchanged")
        with self.assertRaises(ConversionError) as error:
            convert_file(self.root / "absent.step", output)
        self.assertEqual(error.exception.code, "output_exists")
        self.assertEqual((output / "keep.txt").read_text(), "unchanged")

    def test_bad_step_and_parasolid_files_are_rejected(self):
        for extension in ("step", "x_t", "x_b"):
            source = self.root / ("corrupt." + extension)
            source.write_bytes(b"invalid CAD")
            output = self.root / extension
            with self.assertRaises(ConversionError):
                convert_file(source, output, None if extension == "step" else "mm")
            self.assertFalse(output.exists())

    def test_typed_parasolid_bridge_scales_inches_and_rejects_incomplete_geometry(self):
        # This tests the typed B-Rep bridge, not positive parsing of a supplier X_T/X_B.
        from box_fixture import make_box_model

        model = make_box_model()
        path = self.root / "bridge.step"
        with patch("parasolid_kit.read_brep", return_value=SimpleNamespace(brep=model)):
            shape, source, metrics, report = load_source(
                self.root / "dummy.x_t", "parasolid", "in", "0" * 64, path
            )
        self.assertTrue(source["assembly_instances_placed"])
        self.assertTrue(report["passed"])
        np.testing.assert_allclose(
            metrics["dimensions_mm"], np.array([40, 30, 20]) * 25.4, atol=1e-5
        )
        with patch(
            "parasolid_kit.read_brep",
            return_value=SimpleNamespace(brep=replace(model, complete=False)),
        ):
            from parasolid_kit.interop import OcctConversionError

            with self.assertRaises(OcctConversionError):
                load_source(
                    self.root / "dummy.x_t", "parasolid", "mm", "0" * 64, self.root / "bad.step"
                )
        self.assertFalse((self.root / "bad.step").exists())

    def test_sheet_is_reported_as_sheet_and_wire_only_geometry_is_rejected(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln

        face = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0, 40, 0, 30
        ).Face()
        manifest, _ = self.convert_shape(face)
        self.assertFalse(manifest["mesh_validation"]["bodies"][0]["expected_solid"])
        with self.assertRaises(ConversionError):
            shape_metrics(BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Shape())

    def test_private_synthetic_self_test_runs_without_modal(self):
        result = self_test()
        self.assertTrue(result["ok"])
        self.assertEqual(result["dimensions_mm"], [40, 30, 20])
        self.assertEqual(result["artifacts"], ["step", "glb", "stl"])
        self.assertEqual(result["topology"]["solids"], 3)
        self.assertTrue(result["curved_mesh_reimports_passed"])
        self.assertTrue(result["appearance_reimports_passed"])


if __name__ == "__main__":
    unittest.main()
