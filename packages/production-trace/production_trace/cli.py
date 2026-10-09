"""``python -m production_trace`` — look back at any past production job.

Commands:
  history --root DIR [--tool ID] [--limit N]   list jobs (newest first)
  show    --root DIR JOB_ID                    print a job's trace summary
  manual  --root DIR JOB_ID                    print the manual-steps guide
  verify  --root DIR JOB_ID                    re-hash artifacts against the trace
  explain --root DIR JOB_ID --program NAME     have AI write a job-specific guide for that program
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


def _explain(trace_path: Path, args: argparse.Namespace) -> int:
    """Rewrite MANUAL-AI.md for one past job in the chosen program (needs a live config)."""

    import os

    from .ai_manual import (
        AI_MANUAL_FILENAME,
        BUDGET_ENV,
        CONFIG_ENV,
        DEFAULT_BUDGET_ROOT,
        AIManualWriter,
        guide_facts,
    )
    from .guide import GuideLayer, GuideStep, ManualGuide

    config_path = args.provider_config or os.environ.get(CONFIG_ENV, "")
    facts_path = trace_path.parent / "manual-facts.json"
    if not config_path:
        print(f"provider_not_configured: pass --provider-config or set {CONFIG_ENV}", file=sys.stderr)
        return 2
    if not facts_path.is_file():
        print("this job has no manual-facts.json (it ran before AI manuals existed); re-run the job", file=sys.stderr)
        return 2
    raw = json.loads(facts_path.read_text(encoding="utf-8"))
    guide = ManualGuide(program=raw["program"], title=raw.get("title", ""), summary=raw.get("summary", ""),
                        layers=[GuideLayer(**layer) for layer in raw.get("layers", [])],
                        steps=[GuideStep(**step) for step in raw.get("steps", [])], checks=list(raw.get("checks", [])))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    writer = AIManualWriter(json.loads(Path(config_path).read_text(encoding="utf-8")),
                            ledger_root=os.environ.get(BUDGET_ENV, DEFAULT_BUDGET_ROOT))
    if writer.mode != "live":
        print("shadow_mode_only: the AI manual config is in shadow mode", file=sys.stderr)
        return 2
    result = writer.write(guide_facts(guide, tool_id=trace["tool_id"], trace=trace), program=args.program,
                          job_id=f"explain-{trace['job_id']}-{args.program}")
    print(json.dumps(result.metadata(), ensure_ascii=False, indent=2))
    if not result.markdown:
        return 4 if result.status == "needs_input" else 2
    (trace_path.parent / AI_MANUAL_FILENAME).write_text(result.markdown, encoding="utf-8")
    return 0


def _manual_shadow(args: argparse.Namespace) -> int:
    from .ai_manual import AIManualWriter
    from .ai_manual_eval import run_manual_shadow

    writer = AIManualWriter(json.loads(Path(args.provider_config).read_text(encoding="utf-8")), ledger_root=args.budget_root)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    dev = json.loads(Path(args.split).read_text(encoding="utf-8"))["dev"] if args.split else []
    report = run_manual_shadow(writer, cases, dev_ids=dev)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0 if report["gate"]["passed"] else 6


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
    explain = commands.add_parser("explain")
    explain.add_argument("--root", required=True)
    explain.add_argument("job_id")
    explain.add_argument("--program", required=True, help="program to explain in, for example 'Affinity Designer 2'")
    explain.add_argument("--provider-config", default=None, help="AI manual provider config (default: $PRODUCTION_AI_MANUAL_CONFIG)")
    shadow = commands.add_parser("ai-manual-shadow")
    shadow.add_argument("--provider-config", required=True)
    shadow.add_argument("--cases", required=True)
    shadow.add_argument("--split", default=None)
    shadow.add_argument("--budget-root", default=".runtime/ai-manual-budget")
    shadow.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "ai-manual-shadow":
        return _manual_shadow(args)
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
    if args.command == "explain":
        return _explain(trace_path, args)
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
