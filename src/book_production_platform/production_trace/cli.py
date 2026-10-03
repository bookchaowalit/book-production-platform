"""``python -m production_trace`` — look back at any past production job.

Commands:
  history --root DIR [--tool ID] [--limit N]   list jobs (newest first)
  show    --root DIR JOB_ID                    print a job's trace summary
  manual  --root DIR JOB_ID                    print the manual-steps guide
  verify  --root DIR JOB_ID                    re-hash artifacts against the trace
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .trace import MANUAL_FILENAME, read_history, verify_trace


def _find(root: Path, job_id: str) -> dict[str, Any] | None:
    return next((item for item in reversed(read_history(root)) if item["job_id"] == job_id), None)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="production-trace")
    commands = parser.add_subparsers(dest="command", required=True)
    history = commands.add_parser("history")
    history.add_argument("--root", required=True)
    history.add_argument("--tool", default=None)
    history.add_argument("--limit", type=int, default=50)
    for name in ("show", "manual", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--root", required=True)
        sub.add_argument("job_id")
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "history":
        entries = [item for item in read_history(root) if args.tool in (None, item["tool_id"])]
        for item in list(reversed(entries))[: args.limit]:
            ai = "AI" if item.get("ai_used") else "--"
            print(f"{item['finished_at']}  {item['tool_id']:<20} {item['status']:<10} {ai}  {item['job_id']}  {', '.join(item['artifacts'])}")
        return 0
    entry = _find(root, args.job_id)
    if entry is None:
        print(f"job not found in {root / 'history.jsonl'}: {args.job_id}", file=sys.stderr)
        return 2
    trace_path = root / entry["trace"]
    if args.command == "show":
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        print(f"{trace['tool_id']} job {trace['job_id']} — {trace['status']} ({trace['recipe_version']}, {trace['engine_version']})")
        for step in trace.get("steps", []):
            print(f"  {step['index']:>3}. [{step['stage']}] {step['operation']} {json.dumps(step['params'], ensure_ascii=False)} {step['status']} {step['duration_ms']}ms")
        for artifact in trace.get("artifacts", []):
            print(f"  -> {artifact['file']} {artifact['bytes']} B sha256={artifact['sha256'][:16]}…")
        return 0
    if args.command == "manual":
        manual = trace_path.parent / MANUAL_FILENAME
        if not manual.is_file():
            print("this job has no manual guide", file=sys.stderr)
            return 2
        print(manual.read_text(encoding="utf-8"), end="")
        return 0
    result = verify_trace(root, args.job_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
