# Private CadConvert feasibility proof

This evaluates a second open-source text-Parasolid reader in the existing Modal workspace, without an annual SDK, GPU, warm capacity or production converter replacement. Normal metered CPU/image-build usage still applies. The native service remains 0.5.0; this tooling has its own proof adapter version 0.1.0.

## Milestones

1. **Pin and isolate — complete.** CadConvert commit `d5d9a1b9948c575fd46b22840971360c67878362`, Rust 1.91.1 and a committed Cargo.lock. Strip all bundled vendor material assets, supply an authored empty library and disable finish inference. Retain the upstream license and [notice](../tools/cadconvert/NOTICE.md).
2. **Public proof — complete locally.** Direct reader reports preserve truncation, skipped entities and failed faces that upstream's convenience converter discards. Every raw source face must map and tessellate. A second reader checks face identity/body coverage, then Trimesh checks finite closed geometry after a 0.00001 mm duplicate-seam weld. OpenCASCADE independently checks dimensions and volume. The public upstream calibration body (63 × 103 × 103 mm) passes, including its 10 faces. Malformed/truncated inputs and unknown units fail without publishing output.
3. **Private transport — implemented, deployment pending.** Auto3D 0.91.1 issues a short-lived, origin/job/owner/hash/units/expiry-bound read-only capability. The proof cannot change attempts, source expiry, conversion state or artifacts. CAD goes directly from private R2 to Modal. Only fixed safe check enums return.
4. **Retained supplier proof — pending server run.** The manual `Private CadConvert feasibility proof` workflow accepts an existing failed text-Parasolid job. The initial authorized push has a short, expiring request containing no customer identifiers: it proceeds only if exactly one existing retained failed text-Parasolid upload remains. Ambiguous, new or expired selections abort. No source names, identifiers, dimensions, hashes, face counts, raw errors, CAD or mesh are printed or saved as Actions artifacts.
5. **Production adoption — conditional, disabled.** A validated GLB is a mesh deliverable, not editable STEP. Do not manufacture triangle-wrapped STEP or weaken Auto3D's existing success contract. Revisit an explicit GLB/STL-only fallback only if this proof establishes complete geometry and physical dimensions. Unsupported/partial output is rejected; source appearance/texture fidelity is not promised.

## Run

Use the repository's existing trusted Vault-backed GitHub workflow. It builds only public code on the runner, uses the existing locked Modal client and worker dependencies, runs an ephemeral SDK-authenticated 2-CPU function (4 GiB requested/8 GiB maximum, one container, no retries, 300-second ceiling, scale to zero), checks the public reference inside that image, then obtains a source capability for the approved retained job. All files and raw logs are temporary inside Modal. There is no web submission endpoint or permanent service deployment.

Running `python -m tools.cadconvert.operator` without `--run` reads no credentials and starts no work. Normal later manual runs with a blank job input process the public reference only. The initial expiring request is not ongoing permission to test arbitrary uploads.

## Validation

Local proof tests cover stripped preparation/nonoverwrite, immutable unit choices, limited private transfers and hash enforcement, strict report egress, public geometry and rejected/truncated sources. Six targeted tests passed with public build/source variables. The full Python suite passes 98 tests (two public-build integration checks skipped in the default invocation; both passed in the targeted run). Ruff passes. Hosted proof and deployment evidence will be recorded after execution.
