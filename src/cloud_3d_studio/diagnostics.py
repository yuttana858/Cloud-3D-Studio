"""Geometry-free reports produced inside the bounded native worker."""

import re
from importlib.metadata import PackageNotFoundError, version

from . import __version__
from .failures import failure_code, failure_context

READER_CODES = frozenset(
    "occt.bodies_failed occt.construction_failed occt.faces_dropped occt.healing_unavailable "
    "occt.invalid_geometry occt.invalid_reference occt.invalid_shape occt.invalid_topology "
    "occt.limit_exceeded occt.source_incomplete occt.unknown_orientation occt.unsupported_body "
    "occt.unsupported_curve occt.unsupported_surface occt.unsupported_topology".split()
)
EXCEPTIONS = frozenset(
    "TypeError ValueError AttributeError RuntimeError MemoryError OSError ConversionError "
    "SchemaError ParseError LimitExceededError OcctConversionError InteropError NativeError "
    "Standard_ConstructionError Standard_DomainError Standard_Failure Standard_OutOfRange "
    "Standard_NullObject Standard_NoSuchObject StdFail_NotDone".split()
)
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
CHECKS = dict(
    zip(
        ("sourceRead", "geometryBuild", "geometryValidation", "stepExport", "stepReimport"),
        PHASES[1:6],
        strict=True,
    )
)


def public_code(error):
    code = failure_code(error)
    if code in {
        "cancelled",
        "transfer_failed",
        "lease_expired",
        "dispatch_failed",
        "unsupported_entity",
        "unsupported_format",
        "source_units_required",
        "unsupported_schema",
        "invalid_parasolid",
        "invalid_geometry",
        "complexity_limit",
        "timeout",
        "worker_failed",
    }:
        return code
    if re.fullmatch(r"(body|triangle|input|output)_limit", code):
        return "complexity_limit"
    if code in {"incomplete_conversion", "unsupported_geometry"}:
        return "invalid_geometry"
    if re.search(r"validation|reimport|mismatch", code):
        return "validation_failed"
    return "conversion_failed"


def diagnostic_report(error=None, progress=None):
    """No messages, details, entity identifiers, geometry, names, paths or log text."""
    phase = (progress or {}).get("phase", "read_source")
    if phase not in PHASES:
        phase = "read_source"
    root, reader_code, seen = error, None, set()
    for _ in range(8):
        if root is None or id(root) in seen:
            break
        seen.add(id(root))
        code = getattr(getattr(root, "diagnostic", None), "code", None)
        if code in READER_CODES and reader_code is None:
            reader_code = code
        cause = root.__cause__
        if cause is None or id(cause) in seen:
            break
        root = cause
    category = type(root).__name__ if root is not None else None
    if category is not None and category not in EXCEPTIONS:
        category = "NativeError"

    def dependency(name):
        try:
            value = version(name)
            return value if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,5}", value) else None
        except PackageNotFoundError:
            return None

    return {
        "schemaVersion": 1,
        "versions": {
            "worker": __version__,
            "reader": dependency("parasolid-kit"),
            "occt": dependency("cadquery-ocp-novtk"),
        },
        "outcome": "error_detected" if error else "cad_validated",
        "phase": phase,
        "failureCode": public_code(error) if error else None,
        "readerCode": reader_code,
        "exception": category,
        "frames": failure_context(error)["frames"] if error else [],
        "checks": {
            name: "passed"
            if error is None or PHASES.index(at) < PHASES.index(phase)
            else "failed"
            if at == phase
            else "not_run"
            for name, at in CHECKS.items()
        },
    }


def diagnose(request, directory):
    from pathlib import Path

    from .contracts import validate_options
    from .jobs import digest
    from .native import load_source
    from .progress import ProgressFile

    progress = ProgressFile(directory)
    source = Path(request["source"])
    try:
        kind = validate_options(source.suffix, request["source_unit"], request["quality"])
        load_source(
            source,
            kind,
            request["source_unit"],
            digest(source),
            directory / "diagnostic.step",
            progress=progress,
        )
        return diagnostic_report(progress=progress.latest)
    except Exception as error:
        return diagnostic_report(error, progress.latest)
