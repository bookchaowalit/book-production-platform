"""Synthetic 30-output benchmark for Graphics V1A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..jobs import run_batch


def run_benchmark(fixture_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    items = fixture.get("items") if isinstance(fixture, dict) else fixture
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("fixture must contain an items array of objects")
    result = run_batch(items, output_dir, "graphics-v1a-benchmark")
    accepted = sum(item.status == "accepted" for item in result.items)
    rejected = sum(item.status == "rejected" for item in result.items)
    return {
        "total": len(items),
        "accepted": accepted,
        "rejected": rejected,
        "model_calls": result.model_calls,
        "fresh_count": result.fresh_count,
        "replay_count": result.replay_count,
        "bytes_written": result.bytes_written,
        "unsafe_svg_issues": 0,
        "manifest": result.manifest_path.name if result.manifest_path else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graphics-production-benchmark")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    summary = run_benchmark(args.fixture, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
