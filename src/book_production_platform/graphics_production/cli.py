"""CLI entry point for the local Graphics V1A product."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .export import ExportError, export_batch
from .jobs import IdempotencyConflict, run_batch
from .render import RenderError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphics-production")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("batch", help="render a JSON template batch")
    batch.add_argument("--input", required=True, help="path to a JSON object with an items array")
    batch.add_argument("--output", required=True, help="local output directory")
    batch.add_argument("--idempotency-key", required=True)
    export = commands.add_parser("export", help="print export (high-resolution PNG + PDF) of an accepted batch")
    export.add_argument("--manifest", required=True, help="path to a graphics-batch.v1 manifest")
    export.add_argument("--output", required=True, help="local output directory")
    export.add_argument("--scale", type=int, default=3, help="render scale factor (1-6)")
    export.add_argument("--dpi", type=int, default=300, help="PDF resolution (72-1200)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "export":
        try:
            exported = export_batch(args.manifest, args.output, scale=args.scale, dpi=args.dpi)
        except (ExportError, RenderError) as exc:
            print(f"export error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"manifest": str(exported.manifest_path), "items": exported.items}, ensure_ascii=False))
        return 0
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        print("input error: expected an items array of objects", file=sys.stderr)
        return 2
    try:
        result = run_batch(items, args.output, args.idempotency_key)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status in {"accepted", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
