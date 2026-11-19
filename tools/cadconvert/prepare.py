"""Prepare pinned public engine source; exclude all bundled vendor material assets."""

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

UPSTREAM_COMMIT = "d5d9a1b9948c575fd46b22840971360c67878362"


def prepare(source: Path, work: Path) -> Path:
    if work.exists():
        raise ValueError("Build destination must be new.")
    work.mkdir(parents=True)
    archive = subprocess.check_output(
        ["git", "archive", UPSTREAM_COMMIT, "native", "LICENSE", "README.md"], cwd=source
    )
    upstream = work / "upstream"
    upstream.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(upstream, filter="data")
    assets = upstream / "native/crates/cad-ir/assets"
    shutil.rmtree(assets)
    assets.mkdir()
    # The reader's include_bytes! path remains valid, containing only our empty library.
    (assets / "solidworks-materials.sldmat").write_text("<materials/>\n")
    runner = work / "runner"
    shutil.copytree(Path(__file__).with_name("runner"), runner)
    return runner / "Cargo.toml"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    options = parser.parse_args()
    print(prepare(options.source.resolve(), options.work.resolve()))
