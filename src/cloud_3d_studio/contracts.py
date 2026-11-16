"""Versioned format/units contract shared by CLI and future owned-job connector."""

from dataclasses import dataclass

from . import __version__

UNITS_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
QUALITIES = {"standard": 0.10, "fine": 0.05}


class ConversionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Limits:
    input_bytes: int = 50 * 1024 * 1024
    output_bytes: int = 256 * 1024 * 1024
    total_output_bytes: int = 512 * 1024 * 1024
    triangles: int = 3_000_000
    bodies: int = 10_000
    timeout_seconds: float = 480
    memory_bytes: int = 8 * 1024 * 1024 * 1024

    def __post_init__(self):
        import math

        for name in (
            "input_bytes",
            "output_bytes",
            "total_output_bytes",
            "triangles",
            "bodies",
            "memory_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConversionError("invalid_limit", f"{name} must be a positive integer.")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 900:
            raise ConversionError(
                "invalid_limit", "Timeout must be greater than zero and at most 900 seconds."
            )


INPUTS = {
    "parasolid": {
        "extensions": ["x_t", "x_b"],
        "units": "explicit",
        "family": "cad",
        "coverage": "supported_subset",
    },
    "step": {
        "extensions": ["step", "stp"],
        "units": "embedded",
        "family": "cad",
        "coverage": "single_file_geometry",
    },
}
OUTPUTS = {
    "step": {"extension": "step", "units": "mm", "family": "cad"},
    "glb": {"extension": "glb", "units": "m", "family": "mesh"},
    "stl": {"extension": "stl", "units": "mm", "family": "mesh", "requires_unit_manifest": True},
}
PLANNED = {
    "iges": ["igs", "iges"],
    "brep": ["brep", "brp"],
    "3mf": ["3mf"],
    "mesh": ["obj", "ply", "gltf", "glb", "stl"],
    "drawing": ["dxf", "svg"],
    "artwork": ["pdf", "eps"],
}


def input_format(extension: str) -> str:
    for name, specification in INPUTS.items():
        if extension.lower().lstrip(".") in specification["extensions"]:
            return name
    raise ConversionError(
        "unsupported_format", "This input format is not enabled in the conversion backend."
    )


def validate_options(extension: str, source_unit: str | None, quality: str) -> str:
    kind = input_format(extension)
    if kind == "parasolid" and source_unit not in UNITS_TO_MM:
        raise ConversionError(
            "source_units_required", "Specify actual Parasolid coordinates: mm, cm, m, in or ft."
        )
    if kind == "step" and source_unit is not None:
        raise ConversionError(
            "embedded_units", "STEP uses its declared file units; omit --source-unit."
        )
    if quality not in QUALITIES:
        raise ConversionError("invalid_quality", "Choose standard or fine mesh quality.")
    return kind


def capabilities() -> dict:
    return {
        "schema_version": 1,
        "service": "cloud-3d-studio",
        "version": __version__,
        "inputs": INPUTS,
        "outputs": OUTPUTS,
        "planned": PLANNED,
        "qualities_mm": QUALITIES,
        "cad_feature_history": False,
        "remote_readiness": "requires_staging_validation",
    }
