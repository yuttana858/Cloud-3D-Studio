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
4. **Validate independently.** OCCT validity alone isn't enough: a 4 mm wall on a 2.5 mm fillet repairs to a "valid" but wrong solid. So 25 interior points per source face are classified against the solid. There must be material at half the thickness on the chosen side, and none a quarter thickness on the product side. Any miss fails with `thicken_wall_mismatch`.
5. **Write STEP** and reimport it in a separate process; topology, bounds, area and volume must match.

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

## Next

1. In Auto3D, write the Phase 2 lofted product shell (B-spline walls, trimmed planar floors and plateau) to STEP. Today only the Phase 1 degree-1 shell is written.
2. Run the real Auto3D trays (T1 6-can, Primary Products cans) through this command and record the results here.
3. Add `purpose: thicken` to the owned-job contract in Auto3D and the gateway, with the same capability, lease, hash and publication rules as conversion. Run it through the native proof, then the live canary.
