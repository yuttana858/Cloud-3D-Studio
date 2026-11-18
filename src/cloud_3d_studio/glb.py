"""Appearance-preserving GLB over the independently validated CAD tessellation."""

import hashlib
import io
import json
import struct
from dataclasses import dataclass

import numpy as np
import trimesh

from .appearance import Style
from .contracts import ConversionError


@dataclass(frozen=True)
class ImageTexture:
    """A supplied image, never a path/URL fetched by the converter."""

    content: bytes

    def __post_init__(self):
        from PIL import Image

        if not isinstance(self.content, bytes) or not 0 < len(self.content) <= 16 * 1024 * 1024:
            raise ConversionError("appearance_validation_failed", "Image exceeds texture limits.")
        try:
            with Image.open(io.BytesIO(self.content)) as image:
                if image.format not in ("PNG", "JPEG") or image.width * image.height > 16777216:
                    raise ValueError()
                image.verify()
        except Exception:
            raise ConversionError(
                "appearance_validation_failed", "Unsupported image texture."
            ) from None

    @property
    def checksum(self):
        return hashlib.sha256(self.content).hexdigest()

    def image(self):
        from PIL import Image

        with Image.open(io.BytesIO(self.content)) as image:
            return image.convert("RGBA")


def alpha_mode(style):
    return (
        "BLEND"
        if style.rgba[3] < 1
        or style.texture is not None
        and style.texture.image().getextrema()[3][0] < 255
        else "OPAQUE"
    )


def document(path):
    data = path.read_bytes()
    magic, version, size, json_size, kind = struct.unpack_from("<5I", data)
    if magic != 0x46546C67 or version != 2 or size != len(data) or kind != 0x4E4F534A:
        raise ConversionError("appearance_validation_failed", "Invalid GLB container.")
    tree = json.loads(data[20 : 20 + json_size])
    if any("uri" in item for key in ("images", "buffers") for item in tree.get(key, [])):
        raise ConversionError("appearance_validation_failed", "GLB has external dependencies.")
    return tree


def export_glb(meshes, path):
    scene, expected, palette = trimesh.Scene(), {}, {}
    for index, mesh in enumerate(meshes):
        body = f"body-{index + 1}"
        center = mesh.bounds.mean(axis=0)
        transform = np.eye(4)
        transform[:3, 3] = center * 0.001
        scene.graph.update(frame_to=body, matrix=transform)
        styles = getattr(mesh, "_cad_styles", [Style()])
        assignments = getattr(mesh, "_cad_face_materials", np.zeros(len(mesh.faces), dtype=int))
        uv = getattr(mesh, "_cad_uv", None)
        if len(assignments) != len(mesh.faces):
            raise ConversionError(
                "appearance_validation_failed", "Material assignment is incomplete."
            )
        for material_index in np.unique(assignments):
            style = styles[material_index]
            material_name = f"appearance-{len(palette) + 1}"
            for name, value in palette.items():
                if value.key == style.key:
                    material_name = name
                    break
            palette[material_name] = style
            selected = np.flatnonzero(assignments == material_index)
            # Keep full face-local vertices until after UV assignment. No welding.
            part = trimesh.Trimesh(
                vertices=(mesh.vertices - center) * 0.001,
                faces=mesh.faces[selected],
                process=False,
            )
            if style.texture is not None and (
                uv is None
                or np.asarray(uv).shape != (len(mesh.vertices), 2)
                or not np.isfinite(uv).all()
            ):
                raise ConversionError(
                    "appearance_validation_failed", "Texture has no valid UV mapping."
                )
            material = trimesh.visual.material.PBRMaterial(
                name=material_name,
                baseColorFactor=style.rgba,
                metallicFactor=style.metallic,
                roughnessFactor=style.roughness,
                alphaMode=alpha_mode(style),
                baseColorTexture=style.texture.image() if style.texture is not None else None,
            )
            part.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
            part.remove_unreferenced_vertices()
            name = f"{body}-style-{int(material_index) + 1}"
            scene.add_geometry(part, node_name=name, geom_name=name, parent_node_name=body)
            expected[name] = (body, material_name, len(selected), style.texture is not None)

    def exact_materials(tree):
        # Trimesh's material setter quantizes factors to 8 bits; glTF factors and
        # OCCT Quantity_Color are linear floats. Restore original precision.
        for material in tree.get("materials", []):
            style = palette[material["name"]]
            material["pbrMetallicRoughness"]["baseColorFactor"] = list(style.rgba)

    path.write_bytes(
        scene.export(file_type="glb", include_normals=True, tree_postprocessor=exact_materials)
    )
    return expected, palette


def validate_glb(path, reopened, meshes, evidence, deflection, bodies):
    from .meshes import check_mesh, mesh_metrics

    expected, palette = evidence
    tree = document(path)
    materials = tree.get("materials", [])
    if len(materials) != len(palette) or len(reopened.geometry) != len(expected):
        raise ConversionError("appearance_validation_failed", "GLB lost body/material groups.")
    for material in materials:
        style = palette.get(material.get("name"))
        pbr = material.get("pbrMetallicRoughness", {})
        if style is None or not np.allclose(
            pbr.get("baseColorFactor", []), style.rgba, atol=1e-7, rtol=0
        ):
            raise ConversionError("appearance_validation_failed", "GLB changed a source color.")
        if (
            pbr.get("metallicFactor") != style.metallic
            or pbr.get("roughnessFactor") != style.roughness
            or material.get("alphaMode") != alpha_mode(style)
        ):
            raise ConversionError("appearance_validation_failed", "GLB changed a source material.")
        texture = pbr.get("baseColorTexture")
        if (texture is not None) != (style.texture is not None):
            raise ConversionError("appearance_validation_failed", "GLB lost an image texture.")
        if texture is not None:
            from PIL import Image

            image = reopened.geometry[
                next(name for name, entry in expected.items() if entry[1] == material["name"])
            ].visual.material.baseColorTexture
            if not isinstance(image, Image.Image) or not np.array_equal(
                np.asarray(image.convert("RGBA")), np.asarray(style.texture.image())
            ):
                raise ConversionError("appearance_validation_failed", "GLB image pixels changed.")
    found = set()
    for node in tree["nodes"]:
        if "mesh" not in node:
            continue
        name = node.get("name")
        if name not in expected or name in found:
            raise ConversionError("appearance_validation_failed", "GLB body identity changed.")
        found.add(name)
        _, material_name, triangles, textured = expected[name]
        primitives = tree["meshes"][node["mesh"]]["primitives"]
        if len(primitives) != 1:
            raise ConversionError("appearance_validation_failed", "GLB material group changed.")
        primitive = primitives[0]
        if (
            materials[primitive["material"]]["name"] != material_name
            or tree["accessors"][primitive["indices"]]["count"] != triangles * 3
            or (textured and "TEXCOORD_0" not in primitive["attributes"])
        ):
            raise ConversionError(
                "appearance_validation_failed", "GLB material assignment changed."
            )
    if found != set(expected):
        raise ConversionError("appearance_validation_failed", "GLB lost a material group.")
    for index, original in enumerate(meshes):
        parts = []
        for name, (body, _, _, _) in expected.items():
            if body != f"body-{index + 1}":
                continue
            transform, geometry_name = reopened.graph[name]
            part = reopened.geometry[geometry_name].copy()
            part.apply_transform(transform)
            part.apply_scale(1000)
            parts.append(part)
        actual = trimesh.util.concatenate(parts)
        if len(actual.faces) != len(original.faces):
            raise ConversionError("mesh_validation_failed", "GLB lost body triangles.")
        check_mesh(
            mesh_metrics(original),
            mesh_metrics(actual),
            deflection,
            bodies[index]["expected_solid"],
        )
