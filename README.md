# Cloud 3D Studio

Shared compute services for Auto3D. The first implementation is a CPU-only CAD conversion foundation: supported Parasolid and STEP input to validated STEP, GLB and STL plus a unit/provenance manifest. Blender rendering remains a later, separate worker.

Version: **0.2.0**. See [goals and milestones](docs/conversion-roadmap.md), [format support](docs/formats.md), [operations](docs/operations.md), [credential bridge](docs/modal-credentials.md), and the [Auto3D connector specification](https://github.com/yuttana858/Auto3D/blob/docs/parasolid-local-download/docs/parasolid-modal-integration-plan.md).

## Local development

Use Linux and Python 3.12. From the repository root:

```bash
uv sync --locked
uv run ruff check .
uv run python -m unittest discover -s tests -v
uv run cloud3d capabilities
uv run cloud3d self-test
```

Conversion writes a new output directory and preserves the input:

```bash
uv run cloud3d convert /path/to/product.step --output out/product
uv run cloud3d convert /path/to/product.x_t --source-unit m --output out/product
```

The Parasolid coordinate unit must be explicit; `m` above is only an example. STEP reads its declared units. Inputs and outputs stay local for these commands. The source repository is public: private CAD belongs outside Git or under ignored `private/`, and outputs under ignored `out/`.

The Modal module contains private SDK-authenticated CPU smoke tests and a small proxy-authenticated metadata gateway, with no job-submission web route, GPU or permanent warm pool. Its existence does not mean it has been deployed. The Vault runner reads one complete deployment token pair from Supabase and verifies the actual workspace and environment before any explicit deployment. Remote execution still needs permitted network access and a verified spending policy. See the operations and credential guides.
