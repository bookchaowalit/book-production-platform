"""CLI for local deterministic Data V1A jobs and the V1B brief adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .file_input import FileInputError, is_table_file, table_envelope, table_request
from .jobs import IdempotencyConflict, run_data_job
from .proposal_trace import split_proposal, trace_proposal

if TYPE_CHECKING:  # the AI adapter modules need packages/brief-adapter-kit; deterministic commands never import them
    from .provider import ProviderChartAdapter

DEFAULT_BUDGET_ROOT = ".runtime/data-viz-ai-budget"
PROVIDER_NOT_CONFIGURED = "provider_not_configured"


def _kit_missing() -> bool:
    try:
        import brief_adapter_kit  # noqa: F401
    except ModuleNotFoundError:
        print(f"{PROVIDER_NOT_CONFIGURED}: add packages/brief-adapter-kit to PYTHONPATH for AI adapter commands", file=sys.stderr)
        return True
    return False


def _provider_adapter(config_path: str | None, budget_root: str) -> ProviderChartAdapter:
    from .provider import ProviderChartAdapter, ProviderNotConfigured
    from .provider_config import ProviderConfigError, load_provider_config

    if config_path is None:
        return ProviderChartAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderChartAdapter(provider_config=config, ledger_root=budget_root)


def _run_propose(args: argparse.Namespace) -> int:
    if _kit_missing():
        return 2
    from .provider import SHADOW_MODE_ONLY, ProviderNotConfigured, propose_chart

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(f"{PROVIDER_NOT_CONFIGURED}: {exc}", file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    try:
        if is_table_file(args.input):
            if not args.brief:
                print("input error: --brief is required when --input is a CSV/XLSX file", file=sys.stderr)
                return 2
            envelope = table_envelope(args.input, brief=args.brief, sheet=args.sheet, locale=args.locale)
        else:
            envelope = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except FileInputError as exc:
        print(f"input error: {exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("input error: expected a JSON object", file=sys.stderr)
        return 2
    result = propose_chart(envelope, adapter=adapter, job_id=f"propose-{args.idempotency_key}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_proposal(result.to_dict(), output, args.idempotency_key, envelope=envelope)
    print(json.dumps({"status": result.status, "issue_codes": result.issue_codes, "missing": result.missing, "model_calls": result.model_calls}, ensure_ascii=False))
    if result.status == "needs_input":
        return 4
    return 0 if result.status == "complete" else 2


def _run_shadow(args: argparse.Namespace) -> int:
    if _kit_missing():
        return 2
    from .provider import ProviderNotConfigured
    from .shadow import load_cases, run_shadow

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.cases)
        dev_ids = json.loads(Path(args.split).read_text(encoding="utf-8"))["dev"] if args.split else []
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = run_shadow(adapter, cases, ledger_root=args.budget_root, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-production")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--input", required=True, type=Path, help="DatasetRequest JSON, a complete proposal written by propose, or a CSV/XLSX file")
    build.add_argument("--sheet", default=None, help="XLSX sheet name when --input is a workbook (default: first sheet)")
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--idempotency-key", required=True)
    propose = subparsers.add_parser("propose", help="propose a grounded chart spec from a brief (live provider config only; never builds)")
    propose.add_argument("--input", required=True, help="JSON envelope (dataset_id, csv_text, brief, optional locale/title), or a CSV/XLSX file with --brief")
    propose.add_argument("--brief", default=None, help="the chart brief, required when --input is a CSV/XLSX file")
    propose.add_argument("--sheet", default=None, help="XLSX sheet name when --input is a workbook")
    propose.add_argument("--locale", default=None, help="brief locale when --input is a CSV/XLSX file (e.g. th-TH)")
    propose.add_argument("--output", required=True, help="path for the proposal JSON")
    propose.add_argument("--idempotency-key", required=True)
    propose.add_argument("--provider-config", default=None, help="data-viz-provider-config.v1 JSON (no secrets)")
    propose.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = subparsers.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never builds or uses results")
    shadow.add_argument("--provider-config", required=True, help="data-viz-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="data-viz-eval-split.v1 JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    args = parser.parse_args(argv)
    if args.command == "propose":
        return _run_propose(args)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    if args.command != "build":
        return 2
    try:
        payload = table_request(args.input, sheet=args.sheet) if is_table_file(args.input) else json.loads(args.input.read_text(encoding="utf-8"))
        request, ai_proposal = split_proposal(payload)
        if request is None:
            print(json.dumps({"status": "rejected", "error_code": "proposal_not_complete"}, ensure_ascii=False))
            return 1
        result = run_data_job(request, args.output, args.idempotency_key, ai_proposal=ai_proposal)
    except FileInputError as exc:
        print(json.dumps({"status": "rejected", "error_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    except (OSError, json.JSONDecodeError, IdempotencyConflict) as exc:
        print(json.dumps({"status": "rejected", "error_code": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0 if result.status == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
