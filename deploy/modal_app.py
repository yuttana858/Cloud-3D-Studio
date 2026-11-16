"""Private, CPU-only staging foundation. Importing this module does not deploy it."""

import os
from pathlib import Path

import modal

app = modal.App("cloud-3d-studio")
root = Path(__file__).resolve().parents[1]
image = (
    modal.Image.debian_slim(python_version="3.12")
    # OCCT's wheel links these shared libraries even for headless CAD operations.
    .apt_install("libgl1", "libstdc++6")
    .pip_install_from_requirements(str(Path(__file__).with_name("requirements-worker.lock")))
    .add_local_file(str(root / "LICENSE"), "/opt/cloud3d/LICENSE", copy=True)
    .add_local_file(
        str(root / "THIRD_PARTY_NOTICES.md"), "/opt/cloud3d/THIRD_PARTY_NOTICES.md", copy=True
    )
    .add_local_python_source("cloud_3d_studio")
)


@app.function(
    image=image,
    cpu=(2.0, 2.0),
    memory=(4096, 8192),
    timeout=900,
    retries=0,
    min_containers=0,
    max_containers=1,
    scaledown_window=10,
    enable_memory_snapshot=False,
)
def cad_smoke_test():
    """Synthetic-only proof. Returns checks, never CAD bytes or private customer data."""
    from cloud_3d_studio.jobs import self_test

    return self_test()


@app.function(
    image=image,
    cpu=(0.125, 0.125),
    memory=(128, 256),
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
)
def cad_capabilities():
    from cloud_3d_studio.contracts import capabilities

    return capabilities()


# A separate tiny CPU image avoids loading OCCT just to check the credential bridge.
# Identity is injected by the Vault deployment runner, never inferred from browser input.
gateway_image = (
    modal.Image.debian_slim(python_version="3.12")
    .env(
        {
            "CLOUD3D_WORKSPACE": os.environ.get("CLOUD3D_WORKSPACE", ""),
            "CLOUD3D_ENVIRONMENT": os.environ.get("CLOUD3D_ENVIRONMENT", ""),
        }
    )
    .add_local_python_source("cloud_3d_studio")
)


@app.function(
    image=gateway_image,
    cpu=(0.125, 0.125),
    memory=(128, 256),
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    timeout=30,
    enable_memory_snapshot=False,
)
@modal.asgi_app(requires_proxy_auth=True)
def cad_gateway():
    from cloud_3d_studio.gateway import connection_application

    return connection_application(os.environ)
