"""Map native failures to a fixed public vocabulary without source text or paths."""

import traceback
from pathlib import Path

from .contracts import ConversionError

MESSAGES = {
    "unsupported_schema": "This Parasolid schema is not supported by the installed reader.",
    "unsupported_entity": "This Parasolid geometry is not supported by the installed reader.",
    "invalid_parasolid": "The Parasolid reader could not read the complete source.",
    "complexity_limit": "The source exceeded the bounded conversion resource limits.",
    "invalid_geometry": "The source did not produce complete valid geometry.",
    "worker_failed": "The native converter stopped unexpectedly.",
}


def failure_code(error):
    if isinstance(error, ConversionError):
        return error.code
    if isinstance(error, MemoryError):
        return "complexity_limit"
    from parasolid_kit.errors import LimitExceededError, ParseError, SchemaError

    if isinstance(error, SchemaError):
        return "unsupported_schema"
    if isinstance(error, LimitExceededError):
        return "complexity_limit"
    diagnostic = getattr(error, "diagnostic", None)
    kind = getattr(getattr(diagnostic, "kind", None), "value", None)
    if kind == "unsupported":
        return "unsupported_entity"
    if isinstance(error, ParseError):
        return "invalid_parasolid"
    if type(error).__module__.startswith("parasolid_kit.interop"):
        return "unsupported_entity" if "Unsupported" in type(error).__name__ else "invalid_geometry"
    return "worker_failed"


def failure_context(error):
    """Code locations only: never exception messages, source lines or local variables."""
    known = {
        "TypeError",
        "ValueError",
        "AttributeError",
        "RuntimeError",
        "MemoryError",
        "OSError",
        "ConversionError",
        "SchemaError",
        "ParseError",
        "LimitExceededError",
        "OcctConversionError",
        "InteropError",
    }
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        if "/cloud_3d_studio/" in frame.filename or "/parasolid_kit/" in frame.filename:
            filename = Path(frame.filename).stem
            if filename.replace("_", "").isalnum() and len(filename) <= 64:
                frames.append(filename + ":" + str(frame.lineno))
    return {
        "exception": type(error).__name__ if type(error).__name__ in known else "NativeError",
        "frames": frames[-4:],
    }
