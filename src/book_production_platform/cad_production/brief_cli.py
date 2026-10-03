"""``book-production cad brief`` and ``cad brief-shadow`` (CAD V1B, AI brief → PartIR).

``brief`` proposes a PartIR from a free-text brief through the Central AI
Gateway. It fails closed without an owner-approved provider configuration
(``provider_not_configured``) and refuses a configuration that is still in
shadow mode (``shadow_mode_only``). A complete proposal is written as a
``part-ir.v1`` file for the normal ``cad build`` path; it is never built here.

``brief-shadow`` runs the adapter over EVAL-CAD-brief30 and writes a
metadata-only report; results are never used.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from .provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    CadBriefAdapter,
    ProviderNotConfigured,
)
from .provider_config import ProviderConfigError, load_provider_config

BRIEF_COMMANDS = frozenset({"brief", "brief-shadow"})
DEFAULT_BUDGET_ROOT = ".runtime/cad/ai-budget"
MAX_ENVELOPE_BYTES = 64 * 1024
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class _InvalidArguments(Exception):
    """An argparse error emitted as stable JSON."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _InvalidArguments


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="book-production cad", description="CAD V1B: AI brief → PartIR proposal (proposal only)")
    commands = parser.add_subparsers(dest="command", required=True)
    brief = commands.add_parser("brief", help="propose a part-ir.v1 from a free-text brief (mode=live only)")
    brief.add_argument("--input", type=Path, required=True, help='JSON envelope {"brief", "locale", "part_id"}')
    brief.add_argument("--output", type=Path, required=True, help="path for the proposed part-ir.v1 JSON (new file)")
    brief.add_argument("--job-id", required=True)
    brief.add_argument("--provider-config", type=Path, default=None, help="cad-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", type=Path, default=Path(DEFAULT_BUDGET_ROOT))
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the adapter on EVAL-CAD-brief30; never builds")
    shadow.add_argument("--provider-config", type=Path, required=True)
    shadow.add_argument("--cases", type=Path, action="append", required=True)
    shadow.add_argument("--split", type=Path, default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", type=Path, default=Path(DEFAULT_BUDGET_ROOT))
    shadow.add_argument("--output", type=Path, required=True, help="path for the metadata-only shadow report")
    return parser


def _emit(value: dict[str, Any], *, stream: Any = None) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=stream or sys.stdout)


def _adapter(config_path: Path | None, budget_root: Path) -> CadBriefAdapter:
    if config_path is None:
        return CadBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return CadBriefAdapter(provider_config=config, ledger_root=budget_root)


def _read_envelope(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        payload = stream.read(MAX_ENVELOPE_BYTES + 1)
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError("envelope too large")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("brief"), str):
        raise TypeError("envelope must carry a brief string")
    return value


def _run_brief(args: argparse.Namespace) -> int:
    if not _JOB_ID.fullmatch(args.job_id):
        _emit({"status": "rejected", "error": "invalid_job_id"})
        return 2
    try:
        adapter = _adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured:
        _emit({"status": "rejected", "error": PROVIDER_NOT_CONFIGURED})
        return 2
    if adapter.mode != "live":
        _emit({"status": "rejected", "error": SHADOW_MODE_ONLY})
        print(f"{SHADOW_MODE_ONLY}: run cad brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    if args.output.exists():
        _emit({"status": "rejected", "error": "output_exists"})
        return 2
    try:
        envelope = _read_envelope(args.input)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        _emit({"status": "rejected", "error": "invalid_brief_envelope"})
        return 2
    result = adapter.propose(
        envelope["brief"],
        job_id=args.job_id,
        locale=str(envelope.get("locale", "th-TH")),
        part_id=str(envelope.get("part_id", "brief-part")),
    )
    summary = result.to_dict()
    if result.status == "complete" and result.part_ir is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result.part_ir, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _write_proposal_sidecar(args.output, result, mode=adapter.mode)
    _emit(summary)
    if result.status == "needs_input":
        return 4
    return 0 if result.status == "complete" else 2


def _write_proposal_sidecar(output: Path, result: Any, *, mode: str) -> None:
    """Write ``<output>.proposal.json``: metadata-only provenance that ``cad build`` traces.

    It holds the adapter name/version, mode, model-call count, which dimensions
    the brief provided, and the canonical PartIR hash - never the brief text.
    """

    from book_production_platform.cad_ir import canonical_part_ir, parse_part_ir
    from book_production_platform.production_trace import sha256_json

    from .jobs import PROPOSAL_SCHEMA, PROPOSAL_SIDECAR_SUFFIX

    canonical = json.loads(canonical_part_ir(parse_part_ir(result.part_ir)).decode("utf-8"))
    sidecar = {
        "schema_version": PROPOSAL_SCHEMA,
        "tool_id": "cad-v1b",
        "adapter": f"{result.adapter}/{result.adapter_version}",
        "mode": mode,
        "model_calls": int(result.model_calls),
        "provided": sorted(str(item) for item in result.provided),
        "inferred": [],
        "ir_sha256": sha256_json(canonical),
    }
    path = output.with_name(output.name + PROPOSAL_SIDECAR_SUFFIX)
    path.write_text(json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _run_shadow(args: argparse.Namespace) -> int:
    from .shadow import load_cases, run_shadow

    try:
        adapter = _adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured:
        _emit({"status": "rejected", "error": PROVIDER_NOT_CONFIGURED})
        return 2
    try:
        cases = load_cases(args.cases)
        dev_ids = json.loads(args.split.read_text(encoding="utf-8"))["dev"] if args.split else []
    except (OSError, KeyError, ValueError):
        _emit({"status": "rejected", "error": "invalid_eval_input"})
        return 2
    report = run_shadow(adapter, cases, dev_ids=dev_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit({"gate": report["gate"], "metrics": report["metrics"], "runtime_gate": report["runtime_gate"]})
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def brief_main(argv: Sequence[str]) -> int:
    try:
        args = _parser().parse_args(list(argv))
    except _InvalidArguments:
        _emit({"status": "rejected", "error": "invalid_cli_arguments"})
        return 2
    except SystemExit as exit_:
        return int(exit_.code or 0)
    if args.command == "brief":
        return _run_brief(args)
    return _run_shadow(args)


__all__ = ["BRIEF_COMMANDS", "DEFAULT_BUDGET_ROOT", "brief_main"]
