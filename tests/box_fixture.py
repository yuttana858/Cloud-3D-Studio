# Synthetic B-Rep test fixture from parasolid-kit; this is not an X_T parser fixture.
# Upstream: tests/_occt_fixtures.py at b91972a1ff86d8c8b55b07516923f7c35bfedf77
# Copyright (c) 2026 parasolid-kit contributors. MIT; see FIXTURE-LICENSE.txt.
"""Small exact BrepModel fixtures used by optional OCCT integration tests."""

from __future__ import annotations

from dataclasses import dataclass

from parasolid_kit import (
    Body,
    BodyKind,
    BoundingBox,
    BrepMetrics,
    BrepModel,
    ByteRange,
    CurveGeometry,
    CurveKind,
    Edge,
    Face,
    HalfEdge,
    LineCurve,
    Loop,
    PlaneSurface,
    PointGeometry,
    Region,
    RegionKind,
    Sense,
    Shell,
    SourceNodeRef,
    SurfaceGeometry,
    SurfaceKind,
    TopologyValidation,
    Vector3,
    Vertex,
)


@dataclass
class _Sources:
    index: int = 0

    def next(self, type_name: str) -> SourceNodeRef:
        self.index += 1
        return SourceNodeRef(
            node_index=self.index,
            node_type=10 + self.index,
            type_name=type_name,
            node_id=self.index,
            byte_range=ByteRange(self.index * 10, self.index * 10 + 9),
        )


def make_box_model(
    length: float = 40.0,
    width: float = 30.0,
    height: float = 20.0,
    *,
    _origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    _id_offset: int = 0,
    _source_offset: int = 0,
) -> BrepModel:
    def item_id(local_id: int) -> int:
        return _id_offset + local_id

    x_origin, y_origin, z_origin = _origin
    body_id = item_id(1)
    region_id = item_id(1)
    shell_id = item_id(1)
    sources = _Sources(_source_offset)
    body_source = sources.next("body")
    region_source = sources.next("region")
    shell_source = sources.next("shell")
    positions = {
        item_id(1): (x_origin, y_origin, z_origin),
        item_id(2): (x_origin + length, y_origin, z_origin),
        item_id(3): (x_origin + length, y_origin + width, z_origin),
        item_id(4): (x_origin, y_origin + width, z_origin),
        item_id(5): (x_origin, y_origin, z_origin + height),
        item_id(6): (x_origin + length, y_origin, z_origin + height),
        item_id(7): (x_origin + length, y_origin + width, z_origin + height),
        item_id(8): (x_origin, y_origin + width, z_origin + height),
    }
    point_sources = {item_id: sources.next("point") for item_id in positions}
    vertex_sources = {item_id: sources.next("vertex") for item_id in positions}
    points = tuple(
        PointGeometry(
            id=item_id,
            position=Vector3(*position),
            owner=vertex_sources[item_id],
            source=point_sources[item_id],
        )
        for item_id, position in positions.items()
    )
    vertices = tuple(
        Vertex(
            id=item_id,
            point=item_id,
            tolerance=None,
            owner=body_source,
            source=vertex_sources[item_id],
        )
        for item_id in positions
    )

    face_cycles = (
        (
            item_id(1),
            tuple(item_id(value) for value in (1, 4, 3, 2)),
            (0.0, 0.0, -1.0),
            (1.0, 0.0, 0.0),
        ),
        (
            item_id(2),
            tuple(item_id(value) for value in (5, 6, 7, 8)),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0),
        ),
        (
            item_id(3),
            tuple(item_id(value) for value in (1, 2, 6, 5)),
            (0.0, -1.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        (
            item_id(4),
            tuple(item_id(value) for value in (4, 8, 7, 3)),
            (0.0, 1.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        (
            item_id(5),
            tuple(item_id(value) for value in (1, 5, 8, 4)),
            (-1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ),
        (
            item_id(6),
            tuple(item_id(value) for value in (2, 3, 7, 6)),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ),
    )
    edge_by_pair: dict[tuple[int, int], int] = {}
    edge_vertices: dict[int, tuple[int, int]] = {}
    occurrences: dict[int, list[int]] = {}
    half_edge_data: dict[int, tuple[int, int, int]] = {}
    loop_half_edges: dict[int, tuple[int, ...]] = {}
    next_edge = item_id(1)
    next_half_edge = item_id(1)
    for face_id, cycle, _normal, _x_axis in face_cycles:
        half_edge_ids: list[int] = []
        for start, end in zip(cycle, cycle[1:] + cycle[:1], strict=True):
            key = tuple(sorted((start, end)))
            if key not in edge_by_pair:
                edge_by_pair[key] = next_edge
                edge_vertices[next_edge] = (start, end)
                next_edge += 1
            edge_id = edge_by_pair[key]
            half_edge_data[next_half_edge] = (face_id, edge_id, end)
            occurrences.setdefault(edge_id, []).append(next_half_edge)
            half_edge_ids.append(next_half_edge)
            next_half_edge += 1
        loop_half_edges[face_id] = tuple(half_edge_ids)

    curve_sources = {edge_id: sources.next("line") for edge_id in edge_vertices}
    edge_sources = {edge_id: sources.next("edge") for edge_id in edge_vertices}
    half_edge_sources = {half_edge_id: sources.next("fin") for half_edge_id in half_edge_data}
    loop_sources = {face_id: sources.next("loop") for face_id, *_rest in face_cycles}
    face_sources = {face_id: sources.next("face") for face_id, *_rest in face_cycles}
    surface_sources = {face_id: sources.next("plane") for face_id, *_rest in face_cycles}
    curves = []
    edges = []
    for edge_id, (start, end) in edge_vertices.items():
        start_position = Vector3(*positions[start])
        end_position = Vector3(*positions[end])
        curves.append(
            CurveGeometry(
                id=edge_id,
                sense=Sense.POSITIVE,
                owner=edge_sources[edge_id],
                kind=CurveKind.LINE,
                definition=LineCurve(
                    point=start_position,
                    direction=Vector3(
                        end_position.x - start_position.x,
                        end_position.y - start_position.y,
                        end_position.z - start_position.z,
                    ),
                ),
                source=curve_sources[edge_id],
            )
        )
        edges.append(
            Edge(
                id=edge_id,
                owner=body_source,
                half_edges=tuple(occurrences[edge_id]),
                start_vertex=start,
                end_vertex=end,
                curve=edge_id,
                tolerance=None,
                source=edge_sources[edge_id],
            )
        )
    half_edges = []
    for half_edge_id, (face_id, edge_id, end) in half_edge_data.items():
        loop_members = loop_half_edges[face_id]
        loop_index = loop_members.index(half_edge_id)
        edge_members = occurrences[edge_id]
        half_edges.append(
            HalfEdge(
                id=half_edge_id,
                loop=face_id,
                forward=loop_members[(loop_index + 1) % len(loop_members)],
                backward=loop_members[(loop_index - 1) % len(loop_members)],
                vertex=end,
                other=next(item for item in edge_members if item != half_edge_id),
                edge=edge_id,
                curve=edge_id,
                sense=Sense.POSITIVE,
                dummy=False,
                source=half_edge_sources[half_edge_id],
            )
        )

    loops = tuple(
        Loop(
            id=face_id,
            face=face_id,
            half_edges=loop_half_edges[face_id],
            source=loop_sources[face_id],
        )
        for face_id, *_rest in face_cycles
    )
    surfaces = []
    faces = []
    for face_id, cycle, normal, x_axis in face_cycles:
        surfaces.append(
            SurfaceGeometry(
                id=face_id,
                sense=Sense.POSITIVE,
                owner=face_sources[face_id],
                kind=SurfaceKind.PLANE,
                definition=PlaneSurface(
                    point=Vector3(*positions[cycle[0]]),
                    normal=Vector3(*normal),
                    x_axis=Vector3(*x_axis),
                ),
                source=surface_sources[face_id],
            )
        )
        faces.append(
            Face(
                id=face_id,
                back_shell=shell_id,
                front_shell=shell_id,
                loops=(face_id,),
                surface=face_id,
                sense=Sense.POSITIVE,
                source=face_sources[face_id],
            )
        )

    return BrepModel(
        source_format="text",
        schema_key="synthetic-box",
        complete=True,
        bodies=(
            Body(
                id=body_id,
                kind=BodyKind.SOLID,
                size_resolution=1.0e-6,
                linear_resolution=1.0e-8,
                regions=(region_id,),
                edges=tuple(sorted(edge_vertices)),
                vertices=tuple(positions),
                source=body_source,
            ),
        ),
        regions=(Region(region_id, RegionKind.SOLID, body_id, (shell_id,), region_source),),
        shells=(
            Shell(
                shell_id,
                region_id,
                (),
                tuple(item_id(value) for value in range(1, 7)),
                (),
                None,
                shell_source,
            ),
        ),
        faces=tuple(faces),
        loops=loops,
        half_edges=tuple(half_edges),
        edges=tuple(edges),
        vertices=vertices,
        points=points,
        curves=tuple(curves),
        surfaces=tuple(surfaces),
        topology=TopologyValidation(True, 6, 12, 2),
        metrics=BrepMetrics(
            BoundingBox(
                Vector3(x_origin, y_origin, z_origin),
                Vector3(x_origin + length, y_origin + width, z_origin + height),
            ),
            2.0 * (length * width + width * height + height * length),
            length * width * height,
        ),
        diagnostics=(),
    )
