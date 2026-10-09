"""``python -m file_intake inspect <file>``: what the file is and which tools take it."""

from __future__ import annotations

import argparse
import json
import sys

from .detect import detect
from .routes import routes_for
from .tables import TableReadError, read_table

PREVIEW_ROWS = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="file_intake")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="detect a file and list the tools that take it")
    inspect.add_argument("path")
    inspect.add_argument("--sheet", default=None, help="XLSX sheet name (default: the first sheet)")
    args = parser.parse_args(argv)

    detection = detect(args.path)
    report: dict[str, object] = {
        "schema_version": "file-intake-report.v1",
        "file": detection.to_dict(),
        "routes": [route.to_dict() for route in routes_for(detection.kind)] if detection.ok else [],
    }
    if detection.ok and detection.kind in ("csv", "xlsx"):
        try:
            table = read_table(args.path, sheet=args.sheet)
        except TableReadError as exc:
            report["table"] = {"error": exc.code, "message": str(exc)}
        else:
            report["table"] = {
                "columns": len(table.header),
                "rows": len(table.rows),
                "header": table.header,
                "preview": table.rows[:PREVIEW_ROWS],
                "sheet": table.sheet,
                "sheets": table.sheets,
                "encoding": table.encoding,
                "notes": table.notes,
            }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if detection.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
