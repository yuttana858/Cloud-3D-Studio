"""Molded-pulp tool side: thickening a product-side surface into a validated solid wall."""

import math
import tempfile
import unittest
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from tray_fixture import FILLET_MM, make_tray_surface

from cloud_3d_studio.contracts import ConversionError
from cloud_3d_studio.jobs import thicken_file
from cloud_3d_studio.native import write_step
from cloud_3d_studio.thicken import thicken_shape


class ThickenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surface = make_tray_surface()

    def test_inside_and_outside_walls_follow_the_surface(self):
        for side in ("inside", "outside"):
            with self.subTest(side=side):
                _, report = thicken_shape(self.surface, 1.5, side)
                self.assertEqual(report["source_faces"], 42)
                self.assertGreater(report["wall_check"]["samples"], 500)
                self.assertEqual(report["wall_check"]["missing"], 0)
                self.assertEqual(report["wall_check"]["extra"], 0)
                # Thin wall: volume ~ area x thickness; curvature shifts it a few percent.
                expected = report["surface_area_mm2"] * 1.5
                self.assertTrue(math.isclose(report["volume_mm3"], expected, rel_tol=0.03))

    def test_wall_thicker_than_a_tool_side_fillet_is_refused(self):
        with self.assertRaises(ConversionError) as error:
            thicken_shape(self.surface, FILLET_MM * 1.6, "inside")
        self.assertIn(
            error.exception.code, {"thicken_wall_mismatch", "thicken_invalid", "thicken_failed"}
        )

    def test_rejects_bad_requests(self):
        cases = [
            ((self.surface, 0, "inside"), "thicken_invalid_thickness"),
            ((self.surface, float("nan"), "inside"), "thicken_invalid_thickness"),
            ((self.surface, 25, "inside"), "thicken_invalid_thickness"),
            ((self.surface, 1, "up"), "thicken_invalid_side"),
            (
                (BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 10, 10).Shape(), 1, "inside"),
                "thicken_source_solid",
            ),
        ]
        for arguments, code in cases:
            with self.subTest(code=code), self.assertRaises(ConversionError) as error:
                thicken_shape(*arguments)
            self.assertEqual(error.exception.code, code)

    def test_bounded_job_publishes_validated_step_solid(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder, "product.step"), Path(folder, "tool")
            write_step(self.surface, source)
            result = thicken_file(source, output, 1.2)
            manifest = result["manifest"]
            self.assertTrue(manifest["cad_validation"]["passed"])
            self.assertEqual(manifest["cad"]["topology"]["solids"], 1)
            self.assertEqual(manifest["thicken"]["wall_check"]["missing"], 0)
            self.assertEqual(
                sorted(p.name for p in output.iterdir()), ["manifest.json", "model.step"]
            )

    def test_bounded_job_reports_named_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "product.step")
            write_step(self.surface, source)
            with self.assertRaises(ConversionError) as error:
                thicken_file(source, Path(folder, "tool"), FILLET_MM * 1.6)
            self.assertIn(error.exception.code, {"thicken_wall_mismatch", "thicken_invalid"})
            self.assertFalse(Path(folder, "tool").exists())


if __name__ == "__main__":
    unittest.main()
