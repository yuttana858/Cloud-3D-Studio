# Shared CAD conversion: goals and milestones

Updated November 16, 2026. This supersedes the Blender-first ordering in the original deployment plan while retaining Blender as a later service. The **0.2.0** follow-up adds a Supabase Vault credential bridge and protected metadata gateway; see [setup](modal-credentials.md). M2 still requires real credentials, permitted egress and account cost verification. This does not complete the M3 CAD job connector.

## Goals

1. Make Cloud 3D Studio the shared compute project; conversion is its first CPU service.
2. Reuse one existing Modal workspace and Auto3D's existing identity, private R2 and database. An additional Modal App itself has no separate subscription fee; metered work still consumes shared credits.
3. Convert supported Parasolid and STEP to a validated engineering STEP, browser GLB, neutral STL and explicit metadata. Keep geometry, assembly placements and physical size consistent.
4. Provide a versioned format registry so additional CAD/mesh/drawing adapters can be added without changing job ownership, lifecycle or the Auto3D connector.
5. Keep confidential inputs out of Git, container images, logs and serialized Modal Function payloads.

## Milestones and exit gates

| Milestone | Deliverable and measurable exit gate | Initial status |
| --- | --- | --- |
| M0 — Architecture and scope | Shared project/workspace decision, format/units policy, staged cost policy and this milestone list committed. | Complete in this source increment. |
| M1 — Local CPU foundation, v0.1.0 | Pinned project/lockfile, capabilities, strict Parasolid/STEP adapter, bounded process, STEP/GLB/STL outputs and manifests. Synthetic boxes, inches, curved solids, assemblies, failure/limit tests pass; artifacts reopen and dimensions match. | Locally validated; see operations evidence. |
| M2 — Modal CPU proof | Securely authenticate to the intended workspace; verify plan, remaining credits and net-spend cap; build image and run one bounded private CPU self-test. Record commit, runtime, peak memory, billed usage and validated results. | Blocked on secure Modal connection, cost verification and environment access to Modal hosts. |
| M3 — Owned job connector | Auto3D API creates account-owned jobs; private source/artifact transfers, scoped worker claims, stages, retries, cancellation, expiry and reconciliation pass two-account tests. CAD bytes are not Modal Function arguments/results. | Next after the worker contract is verified. |
| M4 — Auto3D workflow | File/unit/quality controls, correct target-window/session import, cross-scene unit normalization, one-step Undo, downloads and strict shared agent commands pass desktop/mobile checks. | Planned. |
| M5 — Supplier acceptance and pilot | User reference Parasolid and representative assemblies/curves pass known-dimension, count and surface checks through a private staging upload. Allowlisted-account feature flag, quotas, recovery and rollback tested. | Depends on the reference file and preceding gates. |
| M6 — Additional adapters | Introduce one format at a time using the format roadmap and a real-file acceptance corpus. No unsupported capability advertised. | Planned. |

M1 proves the native foundation locally; it does not prove cloud deployment or general Parasolid compatibility. Unsupported source schemas/surfaces can require reader work before M5. Progress and exact validation evidence belong in `operations.md`.

## Shared architecture

```mermaid
flowchart LR
    UI[Auto3D and existing agents] --> API[Existing authenticated Auto3D API]
    API --> DB[(Owned jobs in existing database)]
    API --> R2[(Private R2 artifacts)]
    API --> Studio[Cloud 3D Studio dispatch]
    Studio --> CAD[CPU CAD conversion function]
    Studio --> Blender[Later Blender rendering function]
    CAD <--> R2
    Blender <--> R2
```

Use different images and resource settings for CAD and Blender, even if they belong to one Modal App. The CAD function must not pay for a GPU or load Blender merely to convert geometry. The first code increment contains a local worker and a private SDK smoke function; it intentionally does not expose a web submission route before account-scoped transfers exist.

## Cost and privacy policy

- Initial compute: two capped physical CPU cores, 4 GiB requested / 8 GiB hard-limit memory, one concurrent worker, no minimum warm containers and short idle shutdown. Benchmark before changing limits.
- Use one existing workspace. Credits are workspace-wide and shared with future render jobs. No new paid tier, workspace, GPU or billing-limit increase is part of this increment.
- Before M2, verify applicable remaining credits and the workspace's **net spend limit**. A usage budget applies before credits; it is a different control. No zero-dollar invoice is promised without verifying the account and all applicable charges.
- Modal Function inputs/results can be retained for seven days. M3 passes only expiring job references/capabilities and uses private object transfers for CAD. No source assets in images or logs; temporary directories are cleaned after jobs.
- Reuse Auto3D's identity/storage; do not create another subscription-backed database, login system or always-on API merely for conversion.

References: [Modal Apps](https://modal.com/docs/guide/apps), [pricing](https://modal.com/pricing), [budgets and spend limits](https://modal.com/docs/guide/budgets), [retention](https://modal.com/docs/guide/security).
