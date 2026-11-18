"""Authored public colored geometry; never a supplier fixture."""

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

BODY = (0.2, 0.05, 0.4, 1.0)
FACE = (0.05, 0.3, 0.7, 0.5)
INSTANCE = (0.1, 0.7, 0.2, 1.0)


def color(value):
    return Quantity_ColorRGBA(Quantity_Color(*value[:3], Quantity_TOC_RGB), value[3])


def colored_document(instances=False, nested=False):
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
    part = shapes.AddShape(box, False)
    colors.SetColor(part, color(BODY), XCAFDoc_ColorSurf)
    faces = TopExp_Explorer(box, TopAbs_FACE)
    face_label = shapes.AddSubShape(part, faces.Current())
    colors.SetColor(face_label, color(FACE), XCAFDoc_ColorSurf)
    if instances:
        root = shapes.NewShape()
        shapes.AddComponent(root, part, TopLoc_Location())
        moved = gp_Trsf()
        moved.SetTranslation(gp_Vec(100, 0, 0))
        instance = shapes.AddComponent(root, part, TopLoc_Location(moved))
        colors.SetColor(instance, color(INSTANCE), XCAFDoc_ColorSurf)
        shapes.UpdateAssemblies()
        assert colors.SetInstanceColor(
            shapes.GetShape_s(instance), XCAFDoc_ColorSurf, color(INSTANCE)
        )
        if nested:
            parent = shapes.NewShape()
            rotation = gp_Trsf()
            rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.71)
            shapes.AddComponent(parent, root, TopLoc_Location(rotation))
            shapes.UpdateAssemblies()
    return document


def write_colored_step(path, *, instances=False, nested=False, unit="MM"):
    Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
    Interface_Static.SetCVal_s("write.step.unit", unit)
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetSHUOMode(True)
    assert writer.Transfer(colored_document(instances, nested))
    assert writer.Write(str(path)) == IFSelect_RetDone
