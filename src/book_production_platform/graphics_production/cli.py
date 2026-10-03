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
from .provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    ProviderBriefAdapter,
    ProviderNotConfigured,
)
from .provider_config import ProviderConfigError, load_provider_config
from .render import RenderError

DEFAULT_BUDGET_ROOT = ".runtime/graphics-production-ai-budget"


def _provider_adapter(config_path: str | None, budget_root: str) -> ProviderBriefAdapter:
    if config_path is None:
        return ProviderBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderBriefAdapter(provider_config=config, ledger_root=budget_root)


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
    if not isinstance(envelope, dict) or not isinstance(envelope.get("brief"), str):
        print("input error: expected a JSON object with a brief string", file=sys.stderr)
        return 2
    result = adapter.propose(envelope["brief"], job_id=args.job_id, locale=str(envelope.get("locale", "th-TH")))
    summary = {key: value for key, value in result.to_dict().items() if key != "batch"}
    if result.status == "complete" and result.batch is not None:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result.batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["batch_path"] = str(output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.status == "needs_input":
        print(f"needs_input: provide {', '.join(result.missing) or 'a clearer brief'}", file=sys.stderr)
        return 4
    if result.status != "complete":
        print(f"validation error: {result.issues[0]['code'] if result.issues else 'rejected'}", file=sys.stderr)
        return 2
    return 0


def _run_shadow(args: argparse.Namespace) -> int:
    from .shadow import load_cases, load_split, run_shadow

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.cases)
        dev_ids = load_split(args.split) if args.split else []
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = run_shadow(adapter, cases, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


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
    brief = commands.add_parser("brief", help="propose a template batch from a free-text brief (provider mode=live only)")
    brief.add_argument("--input", required=True, help="path to a JSON object with a brief string and optional locale")
    brief.add_argument("--output", required=True, help="path for the proposed batch JSON (input for the batch command)")
    brief.add_argument("--job-id", required=True, help="unique job id; the ledger allows at most two calls per job")
    brief.add_argument("--provider-config", default=None, help="graphics-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the brief adapter; never renders or uses results")
    shadow.add_argument("--provider-config", required=True, help="graphics-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "brief":
        return _run_brief(args)
    if args.command == "brief-shadow":
        return _run_shadow(args)
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
