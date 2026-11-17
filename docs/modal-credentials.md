# Modal credentials from Auto3D's Supabase Vault

After saving the Vault entry, follow the [manual GitHub connection check](modal-connection-check.md) to authenticate from a trusted runner without copying Modal tokens out of Vault.

Cloud 3D Studio **0.2.0** adds a lightweight connection gateway and trusted deployment runner. Both are prepared for review; neither has been deployed to Modal. The native conversion contract remains STEP (mm), GLB (m), STL (mm) and the provenance manifest.

Auto3D **0.75.0** owns the administrator form and Pages API. It stores complete target/token pairs as encrypted Supabase Vault secrets using separate `production` / `preview` profiles and `deployment` / `runtime` purposes. See the [Auto3D setup guide](https://github.com/yuttana858/Auto3D/blob/feat/modal-credential-bridge/docs/modal-credentials.md) for the installed service-role RPCs and direct Vault-entry template. Never save plaintext credentials in Git, SQL history, chat or command arguments.

| Purpose | Credential pair | Consumer |
| --- | --- | --- |
| Deployment | `ak-…` / `as-…` | Trusted Python Modal SDK runner |
| App connection | `wk-…` / `ws-…` | Auto3D Pages → protected Web Function |

On a trusted machine with the locked dependencies, bootstrap the existing Supabase server identity through its secret manager: `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (or legacy `SUPABASE_SERVICE_ROLE_KEY`). This key is powerful and must stay outside browsers. The runner reads only `auto3d/modal/v1/<profile>/deployment`, validates the record binding and token type, then checks the actual Modal token workspace and available environment using Modal **1.6.4**. It does not require a local `.modal.toml` or repeat manual Modal token entry.

```bash
# Default: authenticate and verify the target; no deploy or conversion invocation.
uv run python deploy/vault_modal.py --profile production

# Explicit operator action after verification and spending-policy review.
uv run python deploy/vault_modal.py --profile production --deploy
```

The deployment child receives exactly the fetched Modal pair and target identity. Supabase keys and alternate Modal authentication environment variables are removed. Credentials are not command arguments or printed output; provider logs are withheld. The default check makes authenticated control-plane calls but starts no Function. An explicit deploy can incur build/runtime costs. Current development-environment egress blocks Modal API hosts; Vault storage does not remove that restriction. Use a trusted runner with approved Supabase/Modal access. No real credentials or CAD were used for local tests.

`cad_gateway` uses a separate small Python image, CPU 0.125, memory 128 MiB requested / 256 MiB maximum, minimum containers 0, maximum containers 1, idle window 2 seconds, no GPU or snapshots. `@modal.asgi_app(requires_proxy_auth=True)` makes Modal authenticate `Modal-Key` / `Modal-Secret` before the application runs. It serves **GET `/connection`** only, returning protocol/version/workspace/environment/app and `conversionReady: false`. Missing deployment identity returns 503; there is no CAD upload, conversion, arbitrary URL or submission route. A test may start this small Function and use a small amount of compute.

After deployment, copy the gateway base URL from the Modal dashboard. Create Web Function proxy tokens in that workspace, save the complete app connection in Auto3D, and run its saved-connection test. This verifies the Pages → Modal route independently of a user's computer firewall. Do not claim a working runtime connection until this live test passes.

Local tests cover the ASGI response/lifespan, missing identity, unsupported actions, proxy-auth declaration, Vault HTTP redirects and modern/legacy key handling, token-purpose/profile binding, target verification, explicit deployment gating and secret-free output. The existing native conversion tests remain in place. Positive supplier Parasolid evidence, remote CPU proof and owned CAD jobs remain subsequent milestones.
