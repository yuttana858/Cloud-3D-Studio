# Cloud 3D Studio

Shared compute services for Auto3D. The first implementation is a CPU-only CAD conversion foundation: supported Parasolid and STEP input to validated STEP, GLB and STL plus a unit/provenance manifest. Blender rendering remains a later, separate worker.

Version: **0.3.3**. See [goals and milestones](docs/conversion-roadmap.md), [format support](docs/formats.md), [operations](docs/operations.md), [credential bridge](docs/modal-credentials.md), and the [Auto3D connector specification](https://github.com/yuttana858/Auto3D/blob/main/docs/parasolid-modal-integration-plan.md).

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

The Modal module contains private SDK-authenticated CPU functions and a proxy-authenticated job gateway. Only short-lived, attempt-scoped job references cross the gateway. CAD bytes pass through Auto3D's private storage API, never through Modal Function arguments/results. One bounded native worker, zero minimum warm containers and CPU-only images reuse the existing workspace.

After saving the Vault record, use the [manual GitHub connection check](docs/modal-connection-check.md) to verify its Modal authentication from a trusted runner. This check does not deploy or run a CAD conversion.

The production build workflow deploys reviewed main, independently checks a synthetic 40 × 30 × 20 mm box, provisions a runtime proxy pair directly into Vault, then enables submission. A five-minute controller is enabled only when the matching Auto3D connector reports ready. See [owned job operation](docs/owned-jobs.md). Positive supplier Parasolid acceptance is still required; the reader supports a subset of Parasolid, not every kernel version/entity.
