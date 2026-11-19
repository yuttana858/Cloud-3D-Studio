"""One ephemeral SDK-only CPU function; no production deployment or warm capacity."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

from tools.cadconvert.worker import summary

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "work/cadconvert"
app = modal.App("cloud-3d-studio-cadconvert-proof")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libstdc++6")
    .pip_install_from_requirements(str(ROOT / "deploy/requirements-worker.lock"))
    .add_local_file(
        str(BUILD / "proof"),
        "/opt/cadconvert/proof",
        copy=True,
    )
    .add_local_file(
        str(BUILD / "upstream/native/crates/cad-convert/tests/samples/small.x_t"),
        "/opt/cadconvert/public.x_t",
        copy=True,
    )
    .add_local_file(str(BUILD / "upstream/LICENSE"), "/opt/cadconvert/LICENSE-upstream", copy=True)
    .add_local_file(
        str(ROOT / "tools/cadconvert/NOTICE.md"), "/opt/cadconvert/NOTICE.md", copy=True
    )
    .add_local_file(
        str(ROOT / "tools/cadconvert/worker.py"), "/opt/cadconvert/worker.py", copy=True
    )
    .add_local_python_source("cloud_3d_studio")
)


@app.function(
    image=image,
    cpu=(2.0, 2.0),
    memory=(4096, 8192),
    timeout=300,
    retries=0,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    enable_memory_snapshot=False,
)
def private_proof(reference=None):
    # Raw Python/OCCT/Rust stdout, stderr and intermediate audits stay in this
    # temporary server directory, including on crash. No broad account secrets.
    with tempfile.TemporaryDirectory(prefix="proof-driver-") as temporary:
        directory = Path(temporary)
        report = directory / "report.json"
        try:
            with (directory / "private.log").open("wb") as private_log:
                result = subprocess.run(
                    [sys.executable, "/opt/cadconvert/worker.py", str(report)],
                    input=json.dumps({"reference": reference}).encode(),
                    stdout=private_log,
                    stderr=private_log,
                    timeout=260,
                    env={
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "PYTHONPATH": "/root",
                        "RAYON_NUM_THREADS": "2",
                    },
                    check=False,
                )
            if result.returncode or not report.is_file() or report.stat().st_size > 4096:
                return summary("proof_failed")
            value = json.loads(report.read_bytes())
            return summary(
                value.get("code"),
                **{
                    key: value.get("checks", {}).get(key, "not_run")
                    for key in ("sourceIntegrity", "sourceCoverage", "meshClosed", "dimensions")
                },
            )
        except subprocess.TimeoutExpired:
            return summary("proof_timeout")
        except Exception:
            return summary("proof_failed")
