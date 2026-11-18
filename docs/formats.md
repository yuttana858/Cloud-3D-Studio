# Format groundwork

This registry concerns the **Cloud 3D Studio conversion backend**. Auto3D already imports many mesh/CAD formats locally; those existing local imports do not need to be moved to Modal.

| Phase | Input/output | Format | Rules and value |
| --- | --- | --- | --- |
| v0.1 foundation | Input | Parasolid X_T / X_B | Explicit coordinate unit; pinned reader supports a subset of schemas/surfaces; strict complete conversion. Supplier acceptance pending. |
| v0.1 foundation | Input/output | STEP / STP | CAD exchange with declared units; output AP242 millimeters. Geometry/topology, not parametric feature history. Input reader/root/shape and output round-trip checks required. |
| v0.1 foundation | Output | GLB | Default Auto3D display artifact, standard meters, embedded buffers, separate positioned body nodes. STEP body/face colors, occurrence overrides and transparency retained in v0.5.0; no promise of names or original bitmap/UV assets. |
| v0.1 foundation | Output | Binary STL | Neutral mesh, coordinates in mm; unit/provenance manifest required. No standard units, CAD surfaces or useful assembly hierarchy in STL. |
| Next CAD adapters | Input/output | IGES / IGS | Legacy surface-CAD interchange; inspect declared units, trimmed surfaces and sewing behavior with samples. |
| Next CAD adapters | Internal exchange | OCCT BREP | Useful validated-kernel cache/interchange; explicit units and pinned OCCT version. Not a universal format. |
| Next mesh adapters | Input/output | 3MF | Unit-aware manufacturing mesh; preserve supported components and metadata. Validate ZIP limits, paths and references. |
| Next mesh adapters | Input/output | OBJ / PLY / glTF / STL | Useful DCC/scanning/mesh workflows. Explicit units where missing; strict resource handling. No automatic claim of reconstructed exact CAD solids. |
| Separate 2D pipeline | Input/output | DXF / SVG | CAD drawings and packaging dielines; preserve physical units, cut/score layers, curves and text. Reuse existing Auto3D/Packaging capabilities where available. |
| Later graphics pipeline | Input/output | PDF / EPS | Drawing/artwork workflow with separate fidelity and parser/isolation requirements. Not a generic 3D CAD adapter. |
| Separate feasibility | Input | SLDPRT / SLDASM, IPT / IAM, CATPart and other native CAD | May require licensed translators or an explicit STEP export from authoring CAD. Do not advertise general native support from a Parasolid reader. |

## Adapter contract

Every enabled adapter declares extensions, input/output direction, CAD/mesh/drawing family, embedded/explicit units, fidelity limits, reader/exporter versions, resource limits and validation evidence. Planned formats are returned separately from enabled formats; requests for planned ones fail clearly.

All geometry processing uses canonical mm. STEP embeds mm, STL writes mm plus a manifest, and GLB converts positions/translations to meters once. The Auto3D connector must normalize to its existing scene units and preserve the intended coordinate frame. Unit metadata is not a substitute for rescaling actual coordinates.

Mesh quality is explicit and bounded. No silent geometry omission, hole filling, Boolean union, decimation or change of origin to make validation pass. Preserve body placements; report intentionally open sheets separately from solids. Reopen exports and compare counts, bounds, area/volume and mesh quality within documented budgets.

Reader support is tested, not inferred from an extension. The current positive synthetic Parasolid tests use a typed source B-Rep; they do not certify an arbitrary supplier `.x_t` / `.x_b`. A real sample is required before production enablement.

References: [Parasolid reader support](https://github.com/monozukuri-ai/parasolid-kit/blob/main/docs/format-support.md), [glTF meters and coordinate system](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html), [STL format](https://www.loc.gov/preservation/digital/formats/fdd/fdd000504.shtml), [3MF specification](https://3mf.io/specification/).
