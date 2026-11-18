import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
from appearance_fixture import BODY, FACE, INSTANCE, write_colored_step
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from PIL import Image

from cloud_3d_studio.appearance import Style, read_step_document, write_step_document
from cloud_3d_studio.contracts import ConversionError
from cloud_3d_studio.glb import ImageTexture, document, export_glb, validate_glb
from cloud_3d_studio.jobs import convert_file, diagnose_file
from cloud_3d_studio.native import shape_metrics


class StepAppearanceTests(unittest.TestCase):
    def test_body_face_alpha_and_step_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "source.step"
            write_colored_step(path)
            shape, units, appearance = read_step_document(path)
            self.assertTrue(units)
            np.testing.assert_allclose(
                shape_metrics(shape)["dimensions_mm"], [40, 30, 20], atol=1e-5
            )
            BRepMesh_IncrementalMesh(shape, 0.1, False, 0.2, True)
            appearance.prepare_faces()
            values = []
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            while explorer.More():
                values.append(appearance.for_face(explorer.Current()).rgba)
                explorer.Next()
            self.assertEqual(len(values), 6)
            self.assertEqual(sum(np.allclose(value, BODY) for value in values), 5)
            self.assertEqual(sum(np.allclose(value, FACE) for value in values), 1)
            write_step_document(appearance, root / "reopened.step")
            self.assertTrue(appearance.step_verified)
            report = appearance.report(source_only=True)
            self.assertGreaterEqual(report["colors"]["detected"], 2)
            self.assertEqual(report["transparency"]["detected"], 1)
            self.assertEqual(report["imageTextures"]["status"], "not_exposed_by_reader")

    def test_assembly_occurrences_have_styles_at_world_positions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "assembly.step"
            write_colored_step(path, instances=True)
            shape, _, appearance = read_step_document(path)
            self.assertEqual(shape_metrics(shape)["topology"]["solids"], 2)
            BRepMesh_IncrementalMesh(shape, 0.1, False, 0.2, True)
            appearance.prepare_faces()
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            values = []
            while explorer.More():
                style = appearance.for_face(explorer.Current())
                self.assertTrue(style.source_color)
                values.append(style.rgba)
                explorer.Next()
            self.assertEqual(len(values), 12)
            self.assertEqual(sum(np.allclose(value, BODY) for value in values), 5)
            self.assertEqual(sum(np.allclose(value, INSTANCE) for value in values), 5)
            self.assertEqual(sum(np.allclose(value, FACE) for value in values), 2)

    def test_conversion_preserves_appearance_and_physical_assembly(self):
        for instances, nested in ((False, False), (True, False), (True, True)):
            with (
                self.subTest(instances=instances, nested=nested),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                source = root / "source.step"
                write_colored_step(source, instances=instances, nested=nested)
                result = convert_file(source, root / "result")
                report = result["manifest"]["appearance"]
                self.assertEqual(report["validation"], "passed")
                self.assertEqual(report["colors"]["glb"], "preserved")
                self.assertEqual(report["transparency"]["glb"], "preserved")
                self.assertEqual(report["colors"]["step"], "preserved")
                self.assertEqual(report["stl"], "geometry_only")
                materials = document(root / "result" / "model.glb")["materials"]
                for value in (BODY, FACE):
                    self.assertTrue(
                        any(
                            np.allclose(
                                m["pbrMetallicRoughness"]["baseColorFactor"],
                                value,
                                rtol=0,
                                atol=1e-7,
                            )
                            for m in materials
                        )
                    )

    def test_embedded_image_and_uv_roundtrip_and_missing_uv_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "texture.glb"
            image = Image.new("RGBA", (2, 2))
            image.putdata(
                [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 255, 64)]
            )
            content = io.BytesIO()
            image.save(content, format="PNG")
            mesh = trimesh.creation.box(extents=(40, 30, 20))
            mesh._cad_styles = [Style((1, 1, 1, 1), texture=ImageTexture(content.getvalue()))]
            mesh._cad_face_materials = np.zeros(len(mesh.faces), dtype=int)
            with self.assertRaises(ConversionError):
                export_glb([mesh], path)
            mesh._cad_uv = (mesh.vertices[:, :2] + [20, 15]) / [40, 30]
            expected = export_glb([mesh], path)
            reopened = trimesh.load(path, force="scene", process=False)
            validate_glb(path, reopened, [mesh], expected, 0.1, [{"expected_solid": True}])
            tree = document(path)
            self.assertEqual(len(tree["images"]), 1)
            self.assertIn("bufferView", tree["images"][0])
            self.assertNotIn("uri", tree["images"][0])

    def test_texture_input_never_accepts_paths_or_external_links(self):
        for invalid in ("https://example.com/image.png", b"not an image", b""):
            with self.assertRaises(ConversionError):
                ImageTexture(invalid)

    def test_source_inventory_is_separate_from_diagnostic_and_contains_no_cad(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "supplier-name.step"
            write_colored_step(path)
            result = diagnose_file(path, include_appearance=True)
            self.assertEqual(set(result), {"report", "appearance"})
            self.assertEqual(result["report"]["outcome"], "cad_validated")
            report = result["appearance"]
            self.assertEqual(report["colors"], {"detected": 2, "glb": "not_run", "step": "not_run"})
            self.assertEqual(report["validation"], "not_run")
            self.assertEqual(list(Path(temporary).iterdir()), [path])
            import json

            text = json.dumps(result)
            for token in ("supplier", "rgba", "dimensions", "vertices", "path"):
                self.assertNotIn(token, text)
