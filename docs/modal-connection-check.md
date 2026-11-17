# Next step after saving the Vault secret

The November 17, 2026 metadata check found the production deployment record present and well-formed for workspace `yuttana858`, environment `main`, app `cloud-3d-studio`. Token prefixes matched the deployment purpose and no template placeholders remained. This verifies the saved record, not acceptance by Modal.

The current development environment cannot reach the Supabase or Modal API hosts and has no Supabase server identity. The manual **Check Modal connection from Vault** workflow runs the existing verifier on a trusted GitHub-hosted runner. It reads the Modal pair from Vault on every run. Modal credentials are never copied into GitHub secrets, workflow inputs or source files.

## One-time GitHub setup

1. Merge the workflow PR into `Cloud-3D-Studio`'s `main` branch. A new manual workflow becomes available only after its file is on the default branch.
2. Open [Cloud 3D Studio → Settings → Secrets and variables → Actions](https://github.com/yuttana858/Cloud-3D-Studio/settings/secrets/actions).
3. If `SUPABASE_SECRET_KEY` is already configured there, retain it. Otherwise choose **New repository secret**.
4. Set **Name** to `SUPABASE_SECRET_KEY`.
5. Set **Secret** to a server secret key for the existing Auto3D Supabase project `iesepcoxqochycckbwoh`. Use its `sb_secret_…` key, or legacy service-role JWT. This is the Supabase runner identity, not a Modal key or public/publishable key. Obtain it through the project's API-key administration and paste it directly into GitHub's secret editor, not chat or a repository file. Restrict repository write access: a Supabase server key is privileged and can read more than this one Vault record.
6. Click **Add secret**. The public Supabase project URL is fixed in the workflow, so there is no second project identifier to copy.

The existing server key works; a separately named server key can also be used if you want independent revocation. Neither choice creates a second database or duplicates the Modal credentials. Do not rotate a key used by Auto3D without coordinating that app's configuration.

## Run the authentication check

1. Open [Cloud 3D Studio → Actions](https://github.com/yuttana858/Cloud-3D-Studio/actions).
2. Select **Check Modal connection from Vault**.
3. Click **Run workflow**, select branch **main**, choose profile **production**, and confirm **Run workflow**.
4. Open the run. Success includes: **Deployment token accepted; workspace and environment match. No app was deployed.**

The workflow calls the existing `deploy/vault_modal.py` without `--deploy`. It checks the actual Modal token workspace and available environment using the saved pair. It starts no Modal Functions, does not build an image or transfer CAD, and does not change any Cloudflare settings. It uses normal GitHub Actions runner time according to the account's plan; it does not raise spending limits or add a paid project. There are no automatic triggers or scheduled jobs.

If it fails before checkout, the runner's Supabase key has not been configured. A Vault-read error means the server identity, project access or database RPC needs checking; a token rejection/target failure is reported through the verifier's fixed messages. Do not reveal or copy token values into the workflow log. After a successful check, verify the Modal account's spending controls and then explicitly deploy the prepared gateway. Runtime `wk-`/`ws-` credentials and the Auto3D app rollout follow that deployment.

## Implementation and validation

The job is limited to this repository's `main` branch, uses read-only GitHub token permissions, pinned action commits and a hash-locked Modal-only dependency export. Both secret-bearing steps scope the Supabase key locally; dependency installation has no credentials. No cache, artifact or profile stores credentials. The native CAD libraries are excluded from this verifier environment.

Local checks validate the workflow structure, shell syntax, hash-locked installation, and the existing bridge tests in a minimal Python environment. Hosted execution remains pending until the workflow is merged and the GitHub runner can read Vault. Cloud 3D Studio remains version 0.2.0; no application runtime or conversion behavior is changed.
