"""Per-tool CLI for the Scene3D V1B brief adapter.

Run as ``python -m book_production_platform.scene3d.brief_cli <command>``:

- ``brief`` proposes a ``scene-ir.v1`` from a free-text brief; it refuses a
  provider config whose ``mode`` is not ``live`` (``shadow_mode_only``) and
  fails closed without one (``provider_not_configured``). It writes the
  proposed SceneIR for ``book-production scene compose``; it never runs Blender.
- ``brief-shadow`` runs the adapter over EVAL-S3D-brief30 and writes a
  metadata-only report. Exit codes: ``0`` IR gate passed (render gate still
  pending, see the report), ``6`` IR gate failed, ``2`` input/config error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from book_production_platform.scene3d.provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    ProviderBriefAdapter,
    ProviderNotConfigured,
)
from book_production_platform.scene3d.provider_config import (
    ProviderConfigError,
    load_provider_config,
)
from book_production_platform.scene3d.runtime import proposal_path

DEFAULT_BUDGET_ROOT = ".runtime/scene3d-ai-budget"


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
    summary = {key: value for key, value in result.to_dict().items() if key != "scene"}
    if result.status == "complete" and result.scene is not None:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result.scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["scene_path"] = str(output)
        # Metadata-only sidecar: ``scene compose`` reads it so the job trace
        # records that the SceneIR was AI-proposed (never the brief text).
        sidecar = proposal_path(output)
        sidecar.write_text(json.dumps({
            "adapter": result.adapter,
            "adapter_version": result.adapter_version,
            "mode": adapter.mode,
            "model_calls": result.model_calls,
            "presets": dict(result.presets),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["proposal_path"] = str(sidecar)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.status == "needs_input":
        print(f"needs_input: provide {', '.join(result.missing) or 'a clearer brief'}", file=sys.stderr)
        return 4
    if result.status != "complete":
        print(f"validation error: {result.issues[0]['code'] if result.issues else 'rejected'}", file=sys.stderr)
        return 2
    return 0


def _run_shadow(args: argparse.Namespace) -> int:
    from book_production_platform.scene3d.shadow import (
        load_cases,
        load_split,
        run_shadow,
    )

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
    print("IR-level gate passed; the Blender render gate (AC-S3D-01a) is still pending: keep mode=shadow", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scene3d-brief")
    commands = parser.add_subparsers(dest="command", required=True)
    brief = commands.add_parser("brief", help="propose a scene-ir.v1 from a free-text brief (provider mode=live only)")
    brief.add_argument("--input", required=True, help="path to a JSON object with a brief string and optional locale")
    brief.add_argument("--output", required=True, help="path for the proposed scene-ir.v1 JSON")
    brief.add_argument("--job-id", required=True, help="unique job id; the ledger allows at most two calls per job")
    brief.add_argument("--provider-config", default=None, help="scene3d-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the brief adapter; never renders or uses results")
    shadow.add_argument("--provider-config", required=True, help="scene3d-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "brief":
        return _run_brief(args)
    return _run_shadow(args)


if __name__ == "__main__":
    raise SystemExit(main())
