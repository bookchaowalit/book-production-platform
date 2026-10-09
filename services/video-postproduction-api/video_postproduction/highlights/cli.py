"""Command line for AI highlight proposals.

``python -m video_postproduction.highlights.cli propose --request req.json --output out.json``
    Structured ``ranges`` run without any provider configuration. The brief
    route needs ``--provider-config`` in ``mode=live``; otherwise it exits 2
    with ``provider_not_configured`` or ``shadow_mode_only``.

``python -m video_postproduction.highlights.cli brief-shadow --provider-config cfg.json --cases cases.json --output report.json``
    Runs the EVAL-VID-highlight suite in shadow mode and writes a metadata-only
    report; exits 1 when the gate fails.

Only status values and issue codes are printed; transcript text and briefs are
never written to stdout or stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .provider import (
    KIT_MISSING_MESSAGE,
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    propose_highlights,
)

DEFAULT_BUDGET_ROOT = ".runtime/video-highlight-ai-budget"
_CONFIG_EXIT_CODES = frozenset({PROVIDER_NOT_CONFIGURED, SHADOW_MODE_ONLY})


def _load_config(path: str) -> Any:
    try:
        from .provider_config import ProviderConfigError, load_provider_config
    except ImportError:
        print(KIT_MISSING_MESSAGE, file=sys.stderr)
        return None
    try:
        return load_provider_config(path)
    except ProviderConfigError as exc:
        print(f"{PROVIDER_NOT_CONFIGURED}: {exc}", file=sys.stderr)
        return None


def _write_json(path: str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_propose(args: argparse.Namespace) -> int:
    try:
        raw = json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"request_invalid: {type(exc).__name__}", file=sys.stderr)
        return 2
    config = None
    if args.provider_config is not None:
        config = _load_config(args.provider_config)
        if config is None:
            return 2
    result = propose_highlights(raw, provider_config=config, ledger_root=args.budget_root)
    codes = result.issue_codes
    print(f"{result.status}: {', '.join(codes) or 'awaiting_confirmation'}", file=sys.stderr)
    if _CONFIG_EXIT_CODES & set(codes):
        return 2
    _write_json(args.output, result.to_json())
    return 0 if result.status == "proposed" else 1


def _run_shadow(args: argparse.Namespace) -> int:
    try:
        from .provider import HighlightProposer
        from .shadow import load_cases, load_split, run_shadow
    except ImportError:
        print(KIT_MISSING_MESSAGE, file=sys.stderr)
        return 2
    config = _load_config(args.provider_config)
    if config is None:
        return 2
    proposer = HighlightProposer(provider_config=config, ledger_root=args.budget_root)
    report = run_shadow(proposer, load_cases(args.cases), dev_ids=load_split(args.split))
    _write_json(args.output, report)
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-highlights")
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser(
        "propose", help="propose highlight clips awaiting user confirmation; never renders"
    )
    propose.add_argument("--request", required=True, help="video-highlight-request.v1 JSON")
    propose.add_argument(
        "--provider-config",
        default=None,
        help="video-highlight-provider-config.v1 JSON (no secrets)",
    )
    propose.add_argument(
        "--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory"
    )
    propose.add_argument("--output", required=True, help="path for the result JSON")
    shadow = commands.add_parser(
        "brief-shadow", help="shadow-evaluate the provider adapter; never uses results"
    )
    shadow.add_argument(
        "--provider-config",
        required=True,
        help="video-highlight-provider-config.v1 JSON (no secrets)",
    )
    shadow.add_argument(
        "--cases", action="append", required=True, help="eval case JSON file; repeatable"
    )
    shadow.add_argument(
        "--split", default=None, help="JSON with a 'dev' list of case IDs; the rest are held out"
    )
    shadow.add_argument(
        "--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory"
    )
    shadow.add_argument(
        "--output", required=True, help="path for the metadata-only shadow report JSON"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    return _run_propose(args)


if __name__ == "__main__":
    raise SystemExit(main())
