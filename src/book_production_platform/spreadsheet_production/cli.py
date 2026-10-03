"""CLI entry point for the local Spreadsheet V1A product."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .jobs import IdempotencyConflict, run_workbook_job
from .provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    ProviderNotConfigured,
    SpreadsheetBriefAdapter,
)
from .provider_config import ProviderConfigError, load_provider_config
from .scripts.benchmark import run_benchmark

DEFAULT_BUDGET_ROOT = ".runtime/spreadsheet-production-ai-budget"


def _provider_adapter(config_path: str | None, budget_root: str) -> SpreadsheetBriefAdapter:
    if config_path is None:
        return SpreadsheetBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return SpreadsheetBriefAdapter(provider_config=config, ledger_root=budget_root)


def _run_shadow(args: argparse.Namespace) -> int:
    from .shadow import load_cases, load_datasets, run_shadow

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.cases)
        datasets = load_datasets(args.datasets)
        dev_ids = json.loads(Path(args.split).read_text(encoding="utf-8"))["dev"] if args.split else []
    except (OSError, KeyError, ValueError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = run_shadow(adapter, cases, datasets, ledger_root=args.budget_root, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def _run_brief(args: argparse.Namespace) -> int:
    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    try:
        envelope: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("input error: envelope must be an object", file=sys.stderr)
        return 2
    result = adapter.propose(envelope, job_id=f"brief-{args.idempotency_key}")
    summary: dict[str, Any] = {"proposal": result.metadata()}
    if result.status != "complete" or result.request is None:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 4 if result.status == "needs_input" else 2
    try:
        job = run_workbook_job(
            result.request, args.output, args.idempotency_key, ai_proposal={**result.metadata(), "mode": adapter.mode},
        )
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    summary["job"] = job.to_dict()
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if job.status == "accepted" else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spreadsheet-production")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a validated XLSX workbook")
    build.add_argument("--input", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--idempotency-key", required=True)
    benchmark = commands.add_parser("benchmark", help="run the 20-workbook acceptance suite")
    benchmark.add_argument("--fixture", required=True)
    benchmark.add_argument("--output-dir", required=True)
    brief = commands.add_parser("brief", help="propose a workbook from a brief via the AI gateway (mode=live only), then build it")
    brief.add_argument("--input", required=True, help="JSON envelope: workbook_id, brief, locale, dataset{header, rows}")
    brief.add_argument("--output", required=True)
    brief.add_argument("--idempotency-key", required=True)
    brief.add_argument("--provider-config", default=None, help="spreadsheet-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never builds or uses results")
    shadow.add_argument("--provider-config", required=True, help="spreadsheet-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--datasets", required=True, help="synthetic dataset JSON keyed by dataset name")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    if args.command == "brief":
        return _run_brief(args)
    if args.command == "benchmark":
        try:
            summary = run_benchmark(args.fixture, args.output_dir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"benchmark error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["rejected"] == 0 and summary["formula_mismatches"] == 0 and summary["reopen_failures"] == 0 else 1
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    try:
        result = run_workbook_job(payload, args.output, args.idempotency_key)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
