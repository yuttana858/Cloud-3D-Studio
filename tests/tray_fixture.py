"""Synthetic molded-pulp tray surface: drafted, filleted pocket, plateau and open skirt."""

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound

FILLET_MM = 2.5


def _rectangle(width, depth, z):
    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in (
        (-width / 2, -depth / 2),
        (width / 2, -depth / 2),
        (width / 2, depth / 2),
        (-width / 2, depth / 2),
    ):
        polygon.Add(gp_Pnt(x, y, z))
    polygon.Close()
    return polygon.Wire()


def _centre(shape, linear):
    properties = GProp_GProps()
    (BRepGProp.LinearProperties_s if linear else BRepGProp.SurfaceProperties_s)(shape, properties)
    return properties.CentreOfMass()


def make_tray_surface():
    """Product-side faces (unsewn) of a 140 x 100 x 40 mm tray with one 35 mm deep pocket."""
    block = BRepPrimAPI_MakeBox(gp_Pnt(-70, -50, 0), 140, 100, 40).Shape()
    pocket = BRepOffsetAPI_ThruSections(True, True)
    pocket.AddWire(_rectangle(60, 40, 5))
    pocket.AddWire(_rectangle(66, 46, 41))
    pocket.Build()
    tray = BRepAlgoAPI_Cut(block, pocket.Shape()).Shape()
    fillet = BRepFilletAPI_MakeFillet(tray)
    explorer = TopExp_Explorer(tray, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        c = _centre(edge, True)
        if abs(c.X()) < 35 and abs(c.Y()) < 25:
            fillet.Add(FILLET_MM, edge)
        elif c.Z() > 39.9 or (abs(abs(c.X()) - 70) < 1e-6 and abs(abs(c.Y()) - 50) < 1e-6):
            fillet.Add(3.0, edge)
        explorer.Next()
    tray = fillet.Shape()
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    explorer = TopExp_Explorer(tray, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        if _centre(face, False).Z() > 1e-6:  # drop the closing bottom face: the skirt stays open
            builder.Add(compound, face)
        explorer.Next()
    return compound
