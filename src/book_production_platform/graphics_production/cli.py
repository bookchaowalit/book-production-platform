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
        # Metadata only (adapter, calls, chosen templates): ``batch`` copies it
        # into the job trace so the history shows the items were AI-proposed.
        proposal = {
            "adapter": result.adapter,
            "adapter_version": result.adapter_version,
            "mode": adapter.mode,
            "model_calls": result.model_calls,
            "templates": list(result.templates),
            "variants": list(result.variants),
            "shorten_requested": result.shorten_requested,
        }
        output.write_text(json.dumps({**result.batch, "proposal": proposal}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    commands.add_parser("themes", help="list the registered templates (themes): sizes, text slots, and brief words")
    make = commands.add_parser("make", help="render one theme from command-line text (no JSON, no AI)")
    make.add_argument("--theme", required=True, help="template/theme id (see the themes command)")
    make.add_argument("--variant", action="append", default=None, help="square, portrait, story, landscape, or all; repeatable")
    make.add_argument("--set", dest="slots", action="append", default=[], metavar="SLOT=TEXT", help="slot text; repeatable")
    make.add_argument("--primary", default="#14213D", help="brand primary color")
    make.add_argument("--accent", default="#FCA311", help="brand accent color")
    make.add_argument("--output", required=True, help="local output directory")
    make.add_argument("--idempotency-key", required=True)
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the brief adapter; never renders or uses results")
    shadow.add_argument("--provider-config", required=True, help="graphics-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def _make_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    from .themes import VARIANT_SIZES, get_theme

    theme = get_theme(args.theme)
    requested = args.variant or ["all"]
    variants = list(theme.variants) if "all" in requested else requested
    content: dict[str, str] = {}
    for entry in args.slots:
        slot, separator, text = entry.partition("=")
        if not separator or slot not in theme.slots:
            raise ValueError(f"unknown slot {slot!r}; {args.theme} takes: {', '.join(theme.slots)}")
        content[slot] = text
    items = []
    for variant in variants:
        if variant not in theme.variants:
            raise ValueError(f"{args.theme} does not come in {variant}; sizes: {', '.join(theme.variants)}")
        width, height = VARIANT_SIZES[variant]
        items.append({
            "request_id": f"{args.theme}-{variant}", "template_id": args.theme, "variant": variant,
            "canvas": {"width": width, "height": height}, "brand": {"primary": args.primary, "accent": args.accent},
            "content": content,
        })
    return items


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "themes":
        from .themes import load_themes

        print(json.dumps([theme.summary() for theme in load_themes().values()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "make":
        from .themes import ThemeError

        try:
            make_items = _make_items(args)
        except (ValueError, ThemeError) as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return 2
        try:
            made = run_batch(make_items, args.output, args.idempotency_key)
        except IdempotencyConflict as exc:
            print(f"idempotency error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(made.to_dict(), ensure_ascii=False, default=str, indent=2))
        return 0 if made.status in {"accepted", "partial"} else 2
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
        proposal = payload.get("proposal") if isinstance(payload, dict) else None
        result = run_batch(items, args.output, args.idempotency_key, ai_proposal=proposal if isinstance(proposal, dict) else None)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status in {"accepted", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
