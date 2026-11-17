"""Local CLI; cloud job submission is a subsequent account-scoped connector."""

import argparse
import json
from pathlib import Path

from .contracts import ConversionError, capabilities
from .jobs import convert_file, self_test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    commands.add_parser("self-test")
    conversion = commands.add_parser("convert")
    conversion.add_argument("source", type=Path)
    conversion.add_argument("--output", type=Path, required=True)
    conversion.add_argument("--source-unit", choices=("mm", "cm", "m", "in", "ft"))
    conversion.add_argument("--quality", choices=("standard", "fine"), default="standard")
    arguments = parser.parse_args()
    try:
        if arguments.command == "capabilities":
            result = {"ok": True, **capabilities()}
        elif arguments.command == "self-test":
            result = self_test()
        else:
            result = convert_file(
                arguments.source, arguments.output, arguments.source_unit, arguments.quality
            )
    except (ConversionError, OSError) as error:
        result = {
            "ok": False,
            "error": {"code": getattr(error, "code", "io_error"), "message": str(error)},
        }
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["ok"] else 1
