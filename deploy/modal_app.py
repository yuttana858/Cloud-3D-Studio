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
            "CLOUD3D_NATIVE_VERIFIED": os.environ.get("CLOUD3D_NATIVE_VERIFIED", "0"),
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

    async def dispatch(reference):
        call = await cad_convert_job.spawn.aio(reference)
        return call.object_id

    async def cancel(call_id):
        call = modal.FunctionCall.from_id(call_id)
        await call.cancel.aio(terminate_containers=True)

    return connection_application(os.environ, dispatch=dispatch, cancel=cancel)


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
def cad_convert_job(payload):
    from cloud_3d_studio.remote import JobReference, run_owned_job

    return run_owned_job(JobReference.parse(payload))


if os.environ.get("CLOUD3D_CONTROL_SECRET_NAME"):

    @app.function(
        image=gateway_image,
        cpu=(0.125, 0.125),
        memory=(128, 256),
        timeout=60,
        min_containers=0,
        max_containers=1,
        scaledown_window=2,
        schedule=modal.Period(minutes=5),
        secrets=[modal.Secret.from_name(os.environ["CLOUD3D_CONTROL_SECRET_NAME"])],
        enable_memory_snapshot=False,
    )
    def cad_reconcile():
        import urllib.request

        from cloud_3d_studio.remote import NoRedirect

        request = urllib.request.Request(
            "https://auto3d-5av.pages.dev/internal/conversion-control/reconcile",
            method="POST",
            data=b"{}",
            headers={
                "Authorization": "Bearer " + os.environ["AUTO3D_CONVERSION_CONTROL_SECRET"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
                return {"accepted": response.status == 200}
        except Exception:
            return {"accepted": False}
