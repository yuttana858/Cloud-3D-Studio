# CadConvert feasibility proof

Pinned source: https://github.com/omerbasavul/cadconvert, commit `d5d9a1b9948c575fd46b22840971360c67878362`.

The repository root LICENSE declares MIT (copyright 2026 omerbasavul), while its Cargo workspace metadata declares Apache-2.0. Retain the upstream LICENSE with the proof binary; both declarations are permissive. Resolve the metadata discrepancy before distributing an adopted engine.

All bundled material libraries and appearance assets are excluded. The only replacement is an Auto3D-authored empty `<materials/>` library. The adapter disables finish inference; source colors alone may be carried. This is a private evaluation, not an appearance/texture compatibility claim.

The public calibration input is the pinned upstream test fixture, not customer CAD. The adapter and Python tooling are covered by this repository's MIT license. Cargo.lock pins third-party Rust dependencies and their individual licenses remain applicable. No binary or customer CAD is committed to this repository.
