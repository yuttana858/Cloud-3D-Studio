"""Private CAD appearance; only fixed counts/status leave through job reports."""

import math
from dataclasses import dataclass

import numpy as np

from .contracts import ConversionError

MAX_STYLES = 8192


@dataclass(frozen=True)
class Style:
    rgba: tuple = (0.4, 0.4, 0.4, 1.0)
    metallic: float = 0.0
    roughness: float = 1.0
    source_color: bool = False
    source_material: bool = False
    texture: object = None

    def __post_init__(self):
        if len(self.rgba) != 4 or not all(
            isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1
            for v in (*self.rgba, self.metallic, self.roughness)
        ):
            raise ConversionError("appearance_validation_failed", "Invalid source appearance.")

    @property
    def key(self):
        return (*self.rgba, self.metallic, self.roughness, getattr(self.texture, "checksum", None))


def rgba(color):
    rgb = color.GetRGB()
    return (float(rgb.Red()), float(rgb.Green()), float(rgb.Blue()), float(color.Alpha()))


def read_style(style):
    color = rgba(style.GetColorSurfRGBA()) if style.IsSetColorSurf() else None
    material = style.Material()
    metallic, roughness, has_material = 0.0, 1.0, False
    if material is not None:
        if material.HasPbrMaterial():
            pbr = material.PbrMaterial()
            metallic, roughness = float(pbr.Metallic), float(pbr.Roughness)
            if color is None:
                color = rgba(pbr.BaseColor)
            has_material = True
        elif material.HasCommonMaterial():
            common = material.CommonMaterial()
            if color is None:
                c = common.DiffuseColor
                color = (
                    float(c.Red()),
                    float(c.Green()),
                    float(c.Blue()),
                    float(common.Transparency),
                )
                color = (*color[:3], 1.0 - color[3])
            has_material = True
    return Style(
        color or (0.4, 0.4, 0.4, 1.0),
        metallic,
        roughness,
        source_color=style.IsSetColorSurf(),
        source_material=has_material,
    )


class Appearance:
    def __init__(self, source_format, document=None):
        self.source_format, self.document = source_format, document
        self.colors, self.transparent, self.materials = set(), set(), set()
        self.face_styles, self.body_styles = {}, []
        from OCP.TopTools import TopTools_IndexedMapOfShape

        self.faces = TopTools_IndexedMapOfShape()
        self.step_verified = False
        self.glb_verified = False
        if document is not None:
            self._inventory()

    def _inventory(self):
        from OCP.TDF import TDF_LabelSequence
        from OCP.TopLoc import TopLoc_Location
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.XCAFPrs import XCAFPrs, XCAFPrs_IndexedDataMapOfShapeStyle

        shapes = XCAFDoc_DocumentTool.ShapeTool_s(self.document.Main())
        roots = TDF_LabelSequence()
        shapes.GetFreeShapes(roots)
        for index in range(1, roots.Length() + 1):
            styles = XCAFPrs_IndexedDataMapOfShapeStyle()
            XCAFPrs.CollectStyleSettings_s(roots.Value(index), TopLoc_Location(), styles)
            if styles.Extent() > MAX_STYLES:
                raise ConversionError("complexity_limit", "Appearance style limit exceeded.")
            for j in range(1, styles.Extent() + 1):
                self._record(read_style(styles.FindFromIndex(j)))

    def _record(self, style):
        if style.source_color:
            self.colors.add(style.rgba)
            if style.rgba[3] < 1:
                self.transparent.add(style.rgba)
        if style.source_material:
            self.materials.add(style.key)
        if max(len(self.colors), len(self.materials)) > MAX_STYLES:
            raise ConversionError("complexity_limit", "Appearance style limit exceeded.")

    def prepare_faces(self):
        """Resolve occurrence inheritance using OCCT's rendering style iterator."""
        if self.document is None:
            return
        from OCP.BRep import BRep_Tool
        from OCP.RWMesh import RWMesh_FaceIterator
        from OCP.TopAbs import TopAbs_FORWARD
        from OCP.TopLoc import TopLoc_Location
        from OCP.XCAFPrs import XCAFPrs_DocumentExplorer

        explorer = XCAFPrs_DocumentExplorer(self.document, 0)
        while explorer.More():
            node = explorer.Current()
            if not node.IsAssembly:
                faces = RWMesh_FaceIterator(node.Label, node.Location, True, node.Style)
                while faces.More():
                    face = faces.Face()
                    # Depending on the iterator's binding, Face() retains its local
                    # location while NodeTransformed() includes the occurrence.
                    candidates = (face, face.Moved(node.Location))
                    chosen = None
                    targets = [faces.NodeTransformed(i) for i in (1, 2, 3) if i <= faces.NbNodes()]
                    for candidate in candidates:
                        location = TopLoc_Location()
                        mesh = BRep_Tool.Triangulation_s(candidate, location)
                        if mesh is None:
                            continue
                        points = [
                            mesh.Node(i).Transformed(location.Transformation())
                            for i in range(1, len(targets) + 1)
                        ]
                        if all(a.Distance(b) < 1e-9 for a, b in zip(points, targets)):
                            chosen = candidate
                            break
                    if chosen is None:
                        raise ConversionError(
                            "appearance_validation_failed",
                            "Appearance placement did not match CAD.",
                        )
                    style = read_style(faces.Style())
                    key = self.faces.Add(chosen.Oriented(TopAbs_FORWARD))
                    previous = self.face_styles.get(key)
                    if previous is not None and previous.key != style.key:
                        raise ConversionError(
                            "appearance_validation_failed",
                            "Coincident appearance assignments conflict.",
                        )
                    self.face_styles[key] = style
                    self._record(style)
                    faces.Next()
            explorer.Next()

    def for_face(self, face):
        from OCP.TopAbs import TopAbs_FORWARD

        index = self.faces.FindIndex(face.Oriented(TopAbs_FORWARD))
        if self.document is not None and not index:
            raise ConversionError(
                "appearance_validation_failed", "A CAD face has no resolved style."
            )
        return self.face_styles.get(index, Style())

    def report(self, *, source_only=False):
        supported = self.document is not None

        def outcome(count, verified):
            if source_only:
                return "not_run"
            if not supported:
                return "not_supported"
            return "preserved" if count and verified else "not_present" if not count else "not_run"

        return {
            "schemaVersion": 1,
            "sourceFormat": self.source_format,
            "colors": {
                "detected": len(self.colors),
                "glb": outcome(len(self.colors), self.glb_verified),
                "step": outcome(len(self.colors), self.step_verified),
            },
            "transparency": {
                "detected": len(self.transparent),
                "glb": outcome(len(self.transparent), self.glb_verified),
                "step": outcome(len(self.transparent), self.step_verified),
            },
            "materials": {
                "detected": len(self.materials),
                "glb": "partial"
                if self.materials and self.glb_verified and not source_only
                else outcome(len(self.materials), self.glb_verified),
                "step": "not_run"
                if source_only
                else "not_supported"
                if self.materials
                else "not_present",
            },
            "imageTextures": {"detected": None, "status": "not_exposed_by_reader"},
            "stl": "geometry_only",
            "validation": "not_run"
            if source_only
            else "passed"
            if self.step_verified and self.glb_verified
            else "not_run",
        }


def read_step_document(path):
    from OCP.BRep import BRep_Builder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.STEPConstruct import STEPConstruct_ExternRefs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetMatMode(True)
    reader.SetSHUOMode(True)
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ConversionError("invalid_step", "OCCT could not read this STEP file.")
    basic = reader.ChangeReader()
    units = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    basic.FileUnits(*units)
    declared = [units[0].Value(i).ToCString() for i in range(1, units[0].Length() + 1)]
    if not declared:
        raise ConversionError("source_units_required", "STEP has no declared length unit.")
    references = STEPConstruct_ExternRefs(basic.WS())
    references.LoadExternRefs()
    if references.NbExternRefs():
        raise ConversionError(
            "unsupported_entity",
            "Provide a self-contained STEP export; external files are not loaded.",
        )
    basic.SetSystemLengthUnit(1.0)
    roots_expected = basic.NbRootsForTransfer()
    if roots_expected < 1 or basic.TransferRoots() != roots_expected or basic.NbShapes() < 1:
        raise ConversionError("incomplete_step", "Not every STEP root transferred successfully.")
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    if roots_expected < 1 or not reader.Transfer(document):
        raise ConversionError("incomplete_step", "STEP document transfer was incomplete.")
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    shapes.GetFreeShapes(roots)
    if not roots.Length():
        raise ConversionError("incomplete_step", "STEP document contains no transferable shapes.")
    if roots.Length() == 1:
        shape = shapes.GetShape_s(roots.Value(1))
    else:
        builder, shape = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(shape)
        for i in range(1, roots.Length() + 1):
            builder.Add(shape, shapes.GetShape_s(roots.Value(i)))
    return shape, declared, Appearance("step", document)


def write_step_document(appearance, path, unit="MM"):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer

    Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
    Interface_Static.SetCVal_s("write.step.unit", unit)
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetMaterialMode(True)
    writer.SetSHUOMode(True)
    if not writer.Transfer(appearance.document) or writer.Write(str(path)) != IFSelect_RetDone:
        raise ConversionError("step_export_failed", "STEP appearance export failed.")
    _, _, reopened = read_step_document(path)

    def same(left, right):
        return len(left) == len(right) and (
            not left or np.allclose(sorted(left), sorted(right), atol=1e-7, rtol=0)
        )

    if not same(appearance.colors, reopened.colors) or not same(
        appearance.transparent, reopened.transparent
    ):
        raise ConversionError(
            "appearance_validation_failed", "STEP appearance reimport changed styles."
        )
    appearance.step_verified = True
