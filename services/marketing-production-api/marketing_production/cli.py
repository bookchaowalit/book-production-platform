"""CLI entry point for the local Marketing V1A product and the V1B brief adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .jobs import IdempotencyConflict, ReplayIntegrityError, run_campaign_job
from .proposal_trace import split_proposal, trace_proposal

if TYPE_CHECKING:  # the AI adapter modules need packages/brief-adapter-kit; deterministic commands never import them
    from .provider import ProviderCopyAdapter

DEFAULT_BUDGET_ROOT = ".runtime/marketing-ai-budget"
PROVIDER_NOT_CONFIGURED = "provider_not_configured"


def _kit_missing() -> bool:
    try:
        import brief_adapter_kit  # noqa: F401
    except ModuleNotFoundError:
        print(f"{PROVIDER_NOT_CONFIGURED}: add packages/brief-adapter-kit to PYTHONPATH for AI adapter commands", file=sys.stderr)
        return True
    return False


def _provider_adapter(config_path: str | None, budget_root: str) -> ProviderCopyAdapter:
    from .provider import ProviderCopyAdapter, ProviderNotConfigured
    from .provider_config import ProviderConfigError, load_provider_config

    if config_path is None:
        return ProviderCopyAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderCopyAdapter(provider_config=config, ledger_root=budget_root)


def _run_propose(args: argparse.Namespace) -> int:
    if _kit_missing():
        return 2
    from .provider import SHADOW_MODE_ONLY, ProviderNotConfigured, propose_copy

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(f"{PROVIDER_NOT_CONFIGURED}: {exc}", file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    try:
        envelope = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("input error: expected a JSON object", file=sys.stderr)
        return 2
    result = propose_copy(envelope, adapter=adapter, job_id=f"propose-{args.idempotency_key}")
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marketing-production")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a review-only campaign package")
    build.add_argument("--input", required=True, help="campaign object, {campaign: ...}, or a complete proposal written by propose")
    build.add_argument("--output", required=True, help="local output directory")
    build.add_argument("--idempotency-key", required=True)
    propose = commands.add_parser("propose", help="propose grounded per-channel copy from a brief (live provider config only; never publishes)")
    propose.add_argument("--input", required=True, help="JSON envelope: campaign fields without assets, brand_rules, brief, optional locale")
    propose.add_argument("--output", required=True, help="path for the proposal JSON")
    propose.add_argument("--idempotency-key", required=True)
    propose.add_argument("--provider-config", default=None, help="marketing-provider-config.v1 JSON (no secrets)")
    propose.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never builds or uses results")
    shadow.add_argument("--provider-config", required=True, help="marketing-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="marketing-eval-split.v1 JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "propose":
        return _run_propose(args)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    payload, ai_proposal = split_proposal(payload)
    if payload is None:
        print("input error: the proposal is not complete; review it and fix the brief before building", file=sys.stderr)
        return 2
    campaign = payload.get("campaign") if isinstance(payload, dict) and "campaign" in payload else payload
    if not isinstance(campaign, dict):
        print("input error: expected a campaign object", file=sys.stderr)
        return 2
    try:
        result = run_campaign_job(campaign, args.output, args.idempotency_key, ai_proposal=ai_proposal)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    except ReplayIntegrityError as exc:
        print(f"replay error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
