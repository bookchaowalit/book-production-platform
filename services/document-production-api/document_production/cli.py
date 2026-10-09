"""CLI entry point for the local Documents V1A product."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from document_ir import ScanImportRequest
from pydantic import ValidationError

from .jobs import (
    IdempotencyConflict,
    run_document_brief_job,
    run_document_job,
    run_proposal_job,
    run_report_job,
    run_spec_sheet_job,
    run_transcript_job,
)
from .recipe import RECIPE_IDS
from .scan_import import IMAGE_KINDS, TesseractOCRAdapter, run_scan_import, source_kind

DEFAULT_BUDGET_ROOT = ".runtime/document-production-ai-budget"


PROVIDER_NOT_CONFIGURED = "provider_not_configured"
SHADOW_MODE_ONLY = "shadow_mode_only"


def _provider_adapter(config_path: str | None, budget_root: str) -> Any:
    # Imported lazily: deterministic commands must not need the AI adapter kit on PYTHONPATH.
    from .provider import ProviderBriefAdapter, ProviderNotConfigured
    from .provider_config import ProviderConfigError, load_provider_config

    if config_path is None:
        return ProviderBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderBriefAdapter(provider_config=config, ledger_root=budget_root)


def _kit_missing() -> bool:
    try:
        import brief_adapter_kit  # noqa: F401
    except ModuleNotFoundError:
        print(f"{PROVIDER_NOT_CONFIGURED}: add packages/brief-adapter-kit to PYTHONPATH for AI adapter commands", file=sys.stderr)
        return True
    return False


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
    parser = argparse.ArgumentParser(prog="document-production")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="render a structured document request")
    render.add_argument("--input", required=True, help="path to a structured JSON request")
    render.add_argument("--output", required=True, help="local output directory")
    render.add_argument("--idempotency-key", required=True)
    transcript = commands.add_parser("transcript", help="render a verbatim timestamped transcript document request")
    transcript.add_argument("--input", required=True, help="path to a transcript-document-request.v1 JSON payload")
    transcript.add_argument("--output", required=True, help="local output directory")
    transcript.add_argument("--idempotency-key", required=True)
    proposal = commands.add_parser("proposal", help="render a client proposal (proposal-request.v1)")
    proposal.add_argument("--input", required=True, help="path to a proposal-request.v1 JSON payload")
    proposal.add_argument("--output", required=True, help="local output directory")
    proposal.add_argument("--idempotency-key", required=True)
    spec = commands.add_parser("spec-sheet", help="render a specification sheet (spec-sheet-request.v1)")
    spec.add_argument("--input", required=True, help="path to a spec-sheet-request.v1 JSON payload")
    spec.add_argument("--output", required=True, help="local output directory")
    spec.add_argument("--idempotency-key", required=True)
    spec.add_argument("--image-root", default=None, help="folder with the PNG/JPEG files named in images[]")
    report = commands.add_parser("report", help="render a deterministic tabular report document request")
    report.add_argument("--input", required=True, help="path to a report-document-request.v1 JSON payload")
    report.add_argument("--output", required=True, help="local output directory")
    report.add_argument("--idempotency-key", required=True)
    report.add_argument("--chart-root", default=None, help="folder with the PNG/JPEG files named in charts[]; embeds them after sha256 checks")
    brief = commands.add_parser("brief", help="extract and render a free-text document brief")
    brief.add_argument("--input", required=True, help="path to a JSON brief envelope")
    brief.add_argument("--output", required=True, help="local output directory")
    brief.add_argument("--idempotency-key", required=True)
    brief.add_argument(
        "--adapter",
        choices=("rule-based", "provider"),
        default="rule-based",
        help="brief-adapter.v1 implementation; provider fails closed without an approved provider config",
    )
    brief.add_argument("--provider-config", default=None, help="documents-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never renders or uses results")
    shadow.add_argument("--provider-config", required=True, help="documents-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="documents-eval-split.v1 JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    scan = commands.add_parser("scan", help="propose fields from a local PDF, DOCX, or image scan")
    scan.add_argument("--input", required=True, help="path to a JSON scan request (source_pdf or source_file)")
    scan.add_argument("--input-root", required=True, help="root directory containing the source file")
    scan.add_argument("--output", required=True, help="path for the JSON proposal and QA manifest")
    smoke = commands.add_parser("smoke", help="render a bundled structured sample without provider configuration")
    smoke.add_argument(
        "--output",
        default=".runtime/document-production-smoke",
        help="local output directory for the smoke run",
    )
    return parser


SMOKE_REQUEST: dict[str, Any] = {
    "template_id": "invoice",
    "template_version": "1",
    "document_id": "smoke-001",
    "currency": "THB",
    "title": "Smoke test invoice",
    "customer_name": "Smoke test customer",
    "issue_date": "2026-09-22",
    "line_items": [{"description": "Smoke service", "quantity": "2", "unit_price": "10.00", "tax_rate": "7"}],
}


def _run_smoke(output: Path) -> int:
    """Render a bundled structured sample through the deterministic path."""

    try:
        result = run_document_job(dict(SMOKE_REQUEST), output, "smoke-001")
    except IdempotencyConflict as exc:
        print(f"idempotency conflict: {exc}; use a fresh --output directory", file=sys.stderr)
        return 5
    summary = {
        "status": result.status,
        "job_id": result.job_id,
        "model_calls": result.model_calls,
        "qa_status": result.qa.get("status"),
        "artifacts": [artifact["filename"] for artifact in result.artifacts],
        "replayed": bool(result.cost_event.get("replayed")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.status == "pending":
        print("pending: an equivalent smoke job is already running; retry shortly", file=sys.stderr)
        return 3
    if result.status != "accepted":
        print(f"smoke failed: {result.error_code or 'job_failed'}; inspect {output}", file=sys.stderr)
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "smoke":
        return _run_smoke(Path(args.output))
    if args.command == "brief-shadow":
        return _run_shadow(args)
    if args.command not in {"render", "brief", "scan", "transcript", "report", "spec-sheet", "proposal"}:
        return 2
    input_path = Path(args.input)
    try:
        request = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    if args.command == "scan":
        try:
            scan_request = ScanImportRequest.model_validate(request)
        except ValidationError as exc:
            print(f"validation error: scan request is invalid ({len(exc.errors())} issue(s))", file=sys.stderr)
            return 2
        output_path = Path(args.output)
        input_path = input_path.resolve()
        output_resolved = output_path.resolve(strict=False)
        source_resolved = (Path(args.input_root).resolve(strict=False) / scan_request.source_path).resolve(strict=False)
        if output_resolved in {input_path, source_resolved}:
            print("output error: proposal path must not replace an input file", file=sys.stderr)
            return 2

        ocr_adapter = (
            TesseractOCRAdapter.from_system_for_images()
            if source_kind(scan_request.source_path) in IMAGE_KINDS
            else TesseractOCRAdapter.from_system()
        )
        scan_result = run_scan_import(scan_request, input_root=args.input_root, ocr_adapter=ocr_adapter)
        serialized = json.dumps(scan_result.model_dump(mode="json"), ensure_ascii=False, indent=2)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"output error: {exc}", file=sys.stderr)
            return 2
        print(serialized)
        if scan_result.status == "needs_input":
            print("needs_input: review missing or uncertain fields", file=sys.stderr)
            return 4
        if scan_result.status != "complete":
            code = scan_result.issues[0].code if scan_result.issues else "scan_rejected"
            print(f"validation error: {code}", file=sys.stderr)
            return 2
        return 0

    adapter: Any = None
    if args.command == "brief" and args.adapter == "provider":
        if _kit_missing():
            return 2
        from .provider import ProviderNotConfigured

        try:
            adapter = _provider_adapter(args.provider_config, args.budget_root)
        except ProviderNotConfigured as exc:
            print(f"{PROVIDER_NOT_CONFIGURED}: {exc}", file=sys.stderr)
            return 2
        if adapter.mode != "live":
            print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
            return 2
    try:
        if args.command == "brief":
            result = run_document_brief_job(request, args.output, args.idempotency_key, adapter=adapter)
        elif args.command == "transcript":
            result = run_transcript_job(request, args.output, args.idempotency_key)
        elif args.command == "proposal":
            result = run_proposal_job(request, args.output, args.idempotency_key)
        elif args.command == "spec-sheet":
            result = run_spec_sheet_job(request, args.output, args.idempotency_key, image_root=args.image_root)
        elif args.command == "report":
            result = run_report_job(request, args.output, args.idempotency_key, chart_root=args.chart_root)
        else:
            template_id = request.get("template_id") if isinstance(request, dict) else None
            result = run_document_job(
                request, args.output, args.idempotency_key,
                recipe_version=RECIPE_IDS.get(str(template_id), RECIPE_IDS["invoice"]),
            )
    except IdempotencyConflict as exc:
        print(f"idempotency conflict: {exc}; resend the same input or use a new --idempotency-key", file=sys.stderr)
        return 5
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    if result.status == "pending":
        print(f"pending: {result.error_code or 'job_in_progress'}", file=sys.stderr)
        return 3
    if result.status == "needs_input":
        missing = result.metadata.get("extraction", {}).get("missing", [])
        print(f"needs_input: provide {', '.join(missing)}", file=sys.stderr)
        return 4
    if result.status != "accepted":
        print(f"validation error: {result.error_code or 'job_failed'}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
