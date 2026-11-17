"""Bounded, geometry-free progress shared with the isolated native child."""

import json
import time

PHASES = (
    "download_source",
    "read_source",
    "build_geometry",
    "validate_geometry",
    "write_step",
    "validate_step",
    "tessellate",
    "build_mesh",
    "validate_mesh",
    "upload_artifacts",
    "finalizing",
)


def progress_value(phase, completed=None, total=None, unit=None):
    if phase not in PHASES:
        raise ValueError("Invalid conversion phase.")
    if completed is not None or total is not None:
        if (
            type(completed) is not int
            or type(total) is not int
            or not 0 <= completed <= total <= 10**12
            or not total
            or unit not in ("bytes", "faces", "files")
        ):
            raise ValueError("Invalid conversion work count.")
    elif unit is not None:
        raise ValueError("A progress unit requires counts.")
    return {"phase": phase, "completed": completed, "total": total, "unit": unit}


class ProgressFile:
    def __init__(self, directory):
        self.path = directory / "progress.json"
        self.latest = None
        self.written = 0

    def __call__(self, phase, completed=None, total=None, unit=None):
        value = progress_value(phase, completed, total, unit)
        now = time.monotonic()
        changed = not self.latest or self.latest["phase"] != phase
        self.latest = value
        if changed or total is not None and completed == total or now - self.written >= 0.4:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, allow_nan=False))
            temporary.replace(self.path)
            self.written = now


def read_progress(directory):
    try:
        path = directory / "progress.json"
        if path.stat().st_size > 1024:
            return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {"phase", "completed", "total", "unit"}:
            return None
        return progress_value(**value)
    except (OSError, ValueError, TypeError):
        return None
