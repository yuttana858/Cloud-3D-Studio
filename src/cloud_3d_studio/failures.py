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
    "thicken_no_faces": "The source has no surface faces to thicken.",
    "thicken_source_solid": "The source is already a solid; send the product-side surface.",
    "thicken_disconnected": "The surface faces do not join into one connected surface.",
    "thicken_invalid_thickness": "Thickness must be above 0 and at most 20 mm.",
    "thicken_invalid_side": "Side must be inside or outside.",
    "thicken_failed": "OCCT could not offset the surface.",
    "thicken_invalid": "The thickened wall is not one valid solid.",
    "thicken_wall_mismatch": "The thickened wall does not follow the surface everywhere; a fillet "
    "or corner is tighter than the thickness on the tool side.",
    "thicken_unchecked": "Too few surface samples could be checked.",
    "thicken_volume_mismatch": "The thickened wall's volume does not match its surface.",
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
    seen = set()
    # A topology wrapper otherwise hides the actual failing public reader code.
    # Bound cycles/depth and keep the existing two-field, geometry-free contract.
    for _ in range(8):
        if id(error) in seen:
            break
        seen.add(id(error))
        for frame in traceback.extract_tb(error.__traceback__):
            if "/cloud_3d_studio/" in frame.filename or "/parasolid_kit/" in frame.filename:
                filename = Path(frame.filename).stem
                if filename.replace("_", "").isalnum() and len(filename) <= 64:
                    frames.append(filename + ":" + str(frame.lineno))
        cause = error.__cause__
        if cause is None or id(cause) in seen:
            break
        error = cause
    return {
        "exception": type(error).__name__ if type(error).__name__ in known else "NativeError",
        "frames": frames[-4:],
    }
