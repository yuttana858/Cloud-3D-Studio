# Molded pulp thickening — 0.6.0

Auto3D's Molded Pulp module writes the **product side** of a formed part as smooth NURBS: lofted pocket walls, skirt sides, and planar floors and plateau with shared edges (Auto3D `docs/molded-pulp-nurbs.md`, Phase 2). The **tool side** is that surface offset by the pulp thickness. Offsets of concave regions collide, and a heightfield or loft in the browser can't resolve that. OCCT can. This module does that step with the same bounded native worker used for conversion.

Status: **local command and tests only**. It is not yet an owned cloud job: no gateway route, Auto3D purpose or deployment change is included. See "Next" below.

## Command

```bash
uv run cloud3d thicken product-surface.step --thickness 1.5 --side inside --output out/tool
```

- Input: STEP faces (an open shell or loose faces) in declared units. A solid is refused (`thicken_source_solid`).
- `--side inside|outside`: material goes against or along the face normals.
- Output: a new directory with `model.step` (AP242, mm, one solid) and `manifest.json` (`operation: thicken`, source hash, artifact hash, thickness report, CAD metrics and validation).

## Method

1. **Sew** the faces at 0.001 mm into one connected shell, or fail with `thicken_disconnected`.
2. **Offset** with `BRepOffset_MakeOffset` in thickening mode, using intersection joins (collisions trimmed, no rolled arcs), at 0.0001 mm tolerance.
3. **Repair** with `ShapeFix_Shape`, capped at 0.01 mm, only if OCCT's checker rejects the raw result. On the test tray, the offset floor fillets come back with an unorientable wire, and this pass fixes that.
4. **Validate independently.** OCCT's own validity isn't enough. A 4 mm wall on a 2.5 mm fillet repairs to a "valid" but wrong solid. So 25 interior points per source face each cast one line along their own normal through the solid, and points on that line are classified by counting crossings. There must be material at half the thickness on the chosen side and none a quarter thickness on the product side. The first exit must also be at least the requested thickness. Any miss fails with `thicken_wall_mismatch`. OCCT's general point classifier isn't used: it shoots rays in arbitrary directions and misreported points inside correct walls next to large B-spline faces.
5. **Measure volume from a fine mesh.** OCCT's volume integral (`BRepGProp`) is off by a constant on closed, seamed B-spline faces and their offsets: on the Auto3D 6-can tray it reads about 7,000 mm³ low at every thickness, even at 0.1 mm. The mesh volume follows area × thickness. More than 10% away from it fails with `thicken_volume_mismatch`.
6. **Write STEP** and reimport it in a separate process; topology, bounds, area and volume must match.

The input is copied before sewing: a second offset of the same OCCT faces in one process crashed.

## Measured (synthetic tray, `tests/tray_fixture.py`)

The tray is 140 × 100 × 40 mm with skirt, plateau and one 35 mm drafted pocket. The pocket fillets are 2.5 mm, the outer fillets 3 mm. The product side is 42 faces with 38,674 mm² of area.

| Thickness | Inside | Outside |
| --- | --- | --- |
| 1.0 mm | valid, 1,029 / 1,029 samples, 38,269 mm³ | valid, 39,084 mm³ |
| 1.5 mm | valid, 57,103 mm³ (area × t = 58,012) | valid, 58,935 mm³ |
| 2.0 mm | valid, 75,738 mm³ | valid, 78,994 mm³ |
| 3.0 mm | `thicken_invalid` | `thicken_wall_mismatch` |
| 4.0 mm | `thicken_wall_mismatch` (ShapeFix had called it valid) | `thicken_wall_mismatch` |

Each run takes about 2–3 s, most of it the sample classification. The rule that follows: **every tool-side fillet radius must exceed the pulp thickness**. Auto3D should check this before it submits, with the same message.

## Measured (Auto3D 6-can tray, `tests/auto3d_t1_product.step`)

This is Auto3D's Phase 2 product side for six 66 mm cans at 86 mm pitch, Type 2 pulp, 35 mm form depth, skirt on. It has 17 faces: 6 pocket walls, 4 skirt sides, 6 floor planes and the plateau. Edges are shared exactly, it is valid in OCCT and it sews into one shell, open only at the skirt bottom.

| Thickness | Result (inside) |
| --- | --- |
| 0.1–1.8 mm | One valid solid; all 389 samples pass; minimum thickness equals the request; mesh volume within 1.6% of area × t; 3–5 s each |
| 2 mm and up | `thicken_invalid`: OCCT returns separate pieces (4 pocket floors at 2 mm, all 6 at 3 mm) |

The cause is the sharp floor creases. A thickening join has to extend each pocket wall's offset down to the floor's offset plane, and those extensions stop connecting from about 2 mm. These were tried and didn't help:
- the arc join and OCCT's intersection modes;
- splitting closed faces and edges;
- OCCT fillets, which produce C0 surfaces that the offset refuses (`BRepOffset_C0Geometry`).

## Measured: every crease rounded (`tests/auto3d_t1_rounded.step`)

The same tray, with Auto3D rounding every crease:
- 4 mm tangent cubic fillets at the pocket floors, pocket rims and skirt top;
- 10 mm skirt plan corners, so the skirt becomes one closed wall;
- 40 mm extra material.

That gives 47 faces: closed walls and blends are written as two halves, because OCCT reports a face that closes on itself as self-intersecting. The surface passes OCCT's validity and self-intersection checks, and its only free boundary is the skirt bottom.

| Thickness | Result (inside, one offset, no joins) |
| --- | --- |
| 2 mm | One valid solid; all 1,135 samples pass; minimum thickness 2.000 mm; volume 0.5% under area × t; 6 s |
| 3 mm (Type 2 default) | Same, minimum thickness 3.000 mm, 0.7% under; the full bounded job publishes STEP and manifest |
| 4 mm | Same, minimum thickness 3.994 mm, 0.9% under (the 4 mm rim fillet shrinks to zero on the tool side) |

What it took:
- **Floor fillets alone were not enough.** The sharp skirt corners and skirt top still made OCCT split the floors off. Pockets plus plateau, or skirt plus plateau, each thickened fine; the full set did not.
- **Two-part thicken-and-fuse was unreliable.** Thickening the two groups separately and fusing them gave missing wall at 2 mm and failed at 4 mm.
- **Rounding everything worked.** With every crease tangent, OCCT offsets each face and they meet without joins.

Design limits that follow, which Auto3D checks before writing:
- **Rim radius must exceed the pulp thickness.** On the tool side the rim radius shrinks by the thickness.
- **The plateau must hold two rim fillets.** Every strip of plateau, pocket to pocket or pocket to skirt, needs room for both. With the default 20 mm extra material the six-can tray has only 3.5 mm between the corner pockets and the skirt, so 3 mm pulp needs more extra material there.

STEP round trip: offset faces reimport with area and volume within about 1e-5 relative, so thickening compares those at 1e-4. Topology and bounds stay exact.

## Next

1. Run the Primary Products can trays through Auto3D's rounded output and this command; record the results here.
2. Add `purpose: thicken` to the owned-job contract in Auto3D and the gateway, with the same capability, lease, hash and publication rules as conversion. Run it through the native proof, then the live canary.
