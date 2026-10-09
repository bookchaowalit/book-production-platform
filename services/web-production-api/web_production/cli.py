"""CLI entry point for the local Web V1A product (and the V1B brief adapter)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .jobs import IdempotencyConflict, ReplayIntegrityError, run_page_job

DEFAULT_BUDGET_ROOT = ".runtime/web-production-ai-budget"
PROVIDER_NOT_CONFIGURED = "provider_not_configured"


def _kit_missing() -> bool:
    """AI subcommands need the shared kit; the deterministic build path never imports it."""

    try:
        import brief_adapter_kit  # noqa: F401
    except ModuleNotFoundError:
        print(f"{PROVIDER_NOT_CONFIGURED}: add packages/brief-adapter-kit to PYTHONPATH for AI adapter commands", file=sys.stderr)
        return True
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web-production")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a reviewable static landing page")
    build.add_argument("--input", required=True, help="path to a JSON object with a page field")
    build.add_argument("--output", required=True, help="local output directory")
    build.add_argument("--idempotency-key", required=True)
    build.add_argument("--asset-root", default=None, help="folder holding the page's hero_image file")
    brief = commands.add_parser("brief", help="propose a page from a free-text brief (live provider config only) and build it")
    brief.add_argument("--input", required=True, help='path to {"page_id", "brief", "locale"} JSON')
    brief.add_argument("--output", required=True, help="local output directory")
    brief.add_argument("--idempotency-key", required=True)
    brief.add_argument("--provider-config", default=None, help="web-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never builds or uses results")
    shadow.add_argument("--provider-config", required=True, help="web-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="web-eval-split.v1 JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def _provider_adapter(config_path: str | None, budget_root: str) -> Any:
    from .provider import ProviderNotConfigured, ProviderPageAdapter
    from .provider_config import ProviderConfigError, load_provider_config

    if config_path is None:
        return ProviderPageAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderPageAdapter(provider_config=config, ledger_root=budget_root)


def _run_shadow(args: argparse.Namespace) -> int:
    if _kit_missing():
        return 2
    from brief_adapter_kit.shadow import load_cases

    from .provider import ProviderNotConfigured
    from .shadow import run_shadow

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
    report = run_shadow(adapter, cases, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def _run_brief(args: argparse.Namespace, payload: Any) -> int:
    if _kit_missing():
        return 2
    from .provider import SHADOW_MODE_ONLY, ProviderNotConfigured

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(f"{PROVIDER_NOT_CONFIGURED}: {exc}", file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    if not isinstance(payload, dict) or not isinstance(payload.get("brief"), str) or not isinstance(payload.get("page_id"), str):
        print("input error: expected {page_id, brief, locale}", file=sys.stderr)
        return 2
    proposal = adapter.propose(
        payload["brief"],
        page_id=payload["page_id"],
        locale=str(payload.get("locale", "th-TH")),
        job_id=f"brief-{args.idempotency_key}",
    )
    if proposal.status != "complete" or proposal.request is None:
        summary = {"status": proposal.status, "missing": proposal.missing, "issue_codes": sorted({issue["code"] for issue in proposal.issues}), "model_calls": proposal.model_calls}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"{proposal.status}: review the brief; nothing was built", file=sys.stderr)
        return 4 if proposal.status == "needs_input" else 2
    metadata = {
        "adapter": proposal.adapter, "mode": adapter.mode, "model_calls": proposal.model_calls,
        "provided": proposal.provided, "inferred": proposal.inferred,
    }
    return _build(proposal.request, args.output, args.idempotency_key, None, model_calls=proposal.model_calls, proposal=metadata)


def _build(
    page: dict[str, Any], output: str, key: str, asset_root: str | None, *, model_calls: int = 0, proposal: dict[str, Any] | None = None,
) -> int:
    try:
        result = run_page_job(page, output, key, asset_root=asset_root, proposal=proposal)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    except ReplayIntegrityError as exc:
        print(f"replay error: {exc}", file=sys.stderr)
        return 2
    summary = result.to_dict()
    if model_calls:
        summary["proposal_model_calls"] = model_calls
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "accepted" else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    if args.command == "brief":
        return _run_brief(args, payload)
    page = payload.get("page") if isinstance(payload, dict) and "page" in payload else payload
    if not isinstance(page, dict):
        print("input error: expected a page object", file=sys.stderr)
        return 2
    return _build(page, args.output, args.idempotency_key, args.asset_root)


if __name__ == "__main__":
    raise SystemExit(main())
