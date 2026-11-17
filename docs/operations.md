# Operations and validation evidence

## Native progress and failure reporting — 0.3.3

Adds bounded phase/count telemetry and fixed failure categories. Public source-byte, mesh-face and acknowledged-file counts support measured stage percentages; unknown work stays indeterminate. Private server diagnostics contain only an exception category and up to four package code-file/line positions. Exception messages, source lines, local variables and customer geometry are excluded. All 60 native tests passed; hosted [44177459422](https://github.com/yuttana858/Cloud-3D-Studio/actions/runs/44177459422) passed native proof and the legacy-compatible user-API calibration after PR #13. The follow-up canary explicitly verifies negotiated progress once Auto3D 0.82.0 is deployed. The actual reported X_T failed with the older generic code; its cause is pending a normal private-pipeline retry after rollout. No customer CAD was downloaded or checked into source control. Earlier successful STEP calibration is not acceptance of this Parasolid source.

## Pages client identification — 0.3.2

Auto3D 0.79.0 deployed in [run 44147440033](https://github.com/yuttana858/Auto3D/actions/runs/44147440033). The 0.3.1 native proof passed, but its live user-API canary remained pending because the Pages controller returned HTTP 403 before reaching the application. [Cloudflare documents](https://developers.cloudflare.com/pages/platform/known-issues/#browser-integrity-check-on-pages) that Pages rejects the default Python urllib signature and recommends an application-specific User-Agent. Version 0.3.2 sends the truthful Cloud-3D-Studio identity on controller, worker-transfer and canary requests. Authentication, TLS, redirect refusal and existing network policies remain enforced. All **55 local tests**, Ruff and the offline lockfile check pass. The corrected live requests passed in the hosted run below.

## Current state

Version **0.3.2** is live in the existing `yuttana858` workspace, `main` environment, `cloud-3d-studio` app. On November 17, [run 44150179064](https://github.com/yuttana858/Cloud-3D-Studio/actions/runs/44150179064), source `959e1efbb158cc245db01b25e67509af7a40fe62`, passed the native proof, protected gateway readiness, direct Vault provisioning and the actual Auto3D 0.79.0 private user-API calibration. It uploaded the public STEP, verified cross-account denial, converted/reopened it as 40 × 30 × 20 mm, then downloaded and checked every STEP/GLB/STL/manifest hash and unit. Background reconciliation is enabled. The canary deleted its accounts and files; independent follow-up reads confirmed zero canary identities, conversion jobs and objects under the private conversion prefix. Deployment and runtime pairs stay in Vault; no token copying into frontend configuration is required. Supplier Parasolid acceptance and measured billing usage remain pending. Historical evidence below describes its stated version, not current credential availability.

The authoritative sequence and exit gates are in [conversion-roadmap.md](conversion-roadmap.md). Do not label a remote milestone complete based on local tests.

The 0.3.0 candidate passes **45 tests** (25.834 seconds): the native CAD regressions plus owned transfer/cancellation/cleanup, credential-free native environment, fixed-origin dispatch, runtime pair creation/reuse and lost-acknowledgment recovery. Ruff lint/format and conditional scheduled-controller declaration imports pass. These are local source checks; hosted build results are recorded separately after deployment.

## Local checks

Use Linux/Python 3.12 and the committed `uv.lock`:

```bash
uv sync --locked
uv run ruff check .
uv run python -m unittest discover -s tests -v
uv run cloud3d capabilities
uv run cloud3d self-test
```

`self-test` creates synthetic data in a temporary directory, converts and reopens all outputs, and returns a concise report. It does not contact Modal or upload files. Local `convert` uses the same bounded native subprocess foundation; source data is copied to a private staging directory and remains unchanged.

## Modal staging prerequisites

1. Use the owner's existing intended workspace and development environment. Configure deployment identity through secure environment settings or the supported Modal profile flow; never paste tokens in chat/Git.
2. Check the actual plan, applicable remaining compute credits, current usage and a credits-only **net spend** ceiling. Do not confuse it with the before-credit usage budget. GPU and plan upgrades remain outside this milestone.
3. Inspect the installed Modal CLI help and verify read-only authenticated SDK access to the intended workspace. Record the selected environment, not secrets.
4. Review `deploy/modal_app.py`: CPU only, capped concurrency, zero minimum warm containers, no persistent customer-data volume, no public HTTP endpoint.
5. Build/deploy to development and invoke the private synthetic CPU smoke test only after the preceding checks. Exact operational commands and observed results will be recorded after authenticated execution; import/compile checks alone are not a working deployment.
6. Record source/image versions, artifact validation, startup/run time, peak memory and measured usage. The next milestone connects private owned job transfers; do not pass CAD binaries through Function arguments/results.

## Evidence log

Validated on Linux/Python 3.12.14 with Modal SDK 1.6.4 and the committed dependency pins:

- All **20 tests passed** (22.324 seconds), covering enabled/planned formats, required/embedded units, immutable bounded input, worker timeouts and malformed evidence, box exports, STEP inches, cylinders, through-holes, rotated/translated bodies, sheets, limits, existing-output preservation, invalid text/binary Parasolid, typed B-Rep unit scaling and incomplete-source rejection. Positive supplier Parasolid parsing is not established by the typed fixture.
- Synthetic end-to-end self-test: exactly 40 × 30 × 20 mm; six CAD faces, one solid; independent STEP reimport and GLB/STL mesh reimports passed.
- GLB includes explicit normals and preserves sharp CAD face boundaries. Khronos glTF Validator 2.0.0-dev.3.10 returned zero errors, zero warnings and zero information messages for the generated box. This validator was run as a separate development check, not installed in the CPU runtime.
- Auto3D 0.74.0's actual `ImporterGltf` read the generated GLB: one mesh, 24 normals and dimensions 39.9999991 × 29.9999993 × 19.9999996 mm after standard meter interpretation, within 1e-5 mm of the reference box. This verifies engine interchange; target-scene/account/session normalization remains M4.
- Ruff lint and formatting passed. `uv.lock` pins the full development environment; the hashed worker requirements export excludes development packages. Native runtime outputs and environments are Git-ignored and excluded from the source review.
- The locked Modal application definition imports locally and exposes private SDK functions only. Its image build/cloud execution has not been tested. OCCT system-library dependencies are included in the image definition; the native libraries require GL/X11 linkage even for headless operations, which does not provision a GPU.
- `modal profile list` returned no configured profiles. Fresh HTTPS probes to `https://modal.com` and `https://api.modal.com` returned `Tunnel connection failed: 403 Forbidden` from this environment's proxy. This identifies a coding-environment access block, not a diagnosis of the owner's corporate network or Modal account. Add the required Modal destinations to the environment policy and configure the intended secure identity before M2; preserve the proxy and certificate checks.

No Modal worker, GPU, persistent volume, deployment, budget change or paid-tier upgrade was invoked. API/database and user-scene integration are subsequent milestones, not claims of the initial worker. The final source check counts and PR commit are recorded with the review.

## Credential bridge follow-up — 0.2.0

The draft now includes the proxy-authenticated metadata gateway and Supabase Vault runner; see [setup](modal-credentials.md). The new gateway is intentionally limited to connection verification and reports `conversionReady: false`. It has not been deployed. The worker/native checks above describe the original 0.1.0 foundation; the new bridge tests add target/token validation and explicit deployment gating. Runtime/deployment tokens have not been provided. No real Modal authentication, remote image build or supplier conversion is claimed.

Final follow-up validation: **32 tests passed**, including the original 20 CAD checks and 12 gateway/credential/target tests; Ruff lint and formatting passed, the Modal module imported with proxy auth enabled, and the lockfile resolves the local package at 0.2.0. Auto3D's matching candidate passed 914 application/module tests, 72 MCP tests, desktop/mobile credential flows and the real Workers runtime connection check. Supabase's synthetic encrypted read/write/rotation/removal fixture passed with every write rolled back. No Modal compute was invoked. Hosted CI and live provider authentication remain pending.

## Controller and live calibration follow-up — 0.3.1

The scheduled controller is installed with a health guard, avoiding a second manual deployment merely to activate cleanup after Auto3D rollout. A server-only, explicit public-fixture canary uses two temporary identities to test the actual user API and removes all test records/files. The 0.3.1 candidate passes **52 tests** (34.030 seconds before the version-only metadata change); Ruff lint/format pass. Hosted 0.3.1 deployment and its real user-API canary are recorded after the matching Auto3D rollout. The bundled STEP is generated public calibration geometry, never a supplier model and not part of the native container image.
