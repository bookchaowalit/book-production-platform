"""Local JSON command line interface for Data Pipeline Builder V1A."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from book_production_platform.data_pipeline.jobs import (
    PipelineJobError,
    run_pipeline_job,
)
from book_production_platform.data_pipeline_ir.models import PipelineRequest
from book_production_platform.data_pipeline_ir.validation import (
    MAX_PIPELINE_REQUEST_BYTES,
)

from .provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    PipelineBriefAdapter,
    ProviderNotConfigured,
)
from .provider_config import ProviderConfigError, load_provider_config

DEFAULT_BUDGET_ROOT = ".runtime/data-pipeline-ai-budget"


class PipelineInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book-production data")
    commands = parser.add_subparsers(dest="command", required=True)
    pipeline = commands.add_parser("pipeline", help="Data SQL/Pipeline Builder V1A operations")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    build = pipeline_commands.add_parser("build", help="compile and locally plan a typed pipeline")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--output-root", required=True, type=Path)
    run = pipeline_commands.add_parser("run", help="build the plan, then execute it read-only over local rows and export results.csv")
    run.add_argument("--input", required=True, type=Path, help="pipeline-request.v1 JSON")
    run.add_argument("--rows", required=True, type=Path, help="JSON array of source rows (or {\"rows\": [...]}) keyed by column key")
    run.add_argument("--output-root", required=True, type=Path)
    propose = pipeline_commands.add_parser("propose", help="propose a pipeline from a question via the AI gateway (mode=live only), then build it")
    propose.add_argument("--input", required=True, type=Path, help="JSON envelope: pipeline_id, question, locale, dataset{table_name, source_ref, columns}")
    propose.add_argument("--output-root", required=True, type=Path)
    propose.add_argument("--provider-config", default=None, help="data-pipeline-provider-config.v1 JSON (no secrets)")
    propose.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow = pipeline_commands.add_parser("brief-shadow", help="shadow-evaluate the provider adapter; never builds or uses results")
    shadow.add_argument("--provider-config", required=True, help="data-pipeline-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--datasets", required=True, help="synthetic dataset JSON keyed by dataset name")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def _provider_adapter(config_path: str | None, budget_root: str) -> PipelineBriefAdapter:
    if config_path is None:
        return PipelineBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return PipelineBriefAdapter(provider_config=config, ledger_root=budget_root)


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


def _run_propose(args: argparse.Namespace) -> int:
    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    try:
        envelope: Any = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("input error: envelope must be an object", file=sys.stderr)
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    result = adapter.propose(envelope, job_id=f"propose-{stamp}")
    summary: dict[str, Any] = {"proposal": result.metadata()}
    if result.status != "complete" or result.request is None:
        _emit(summary)
        return 4 if result.status == "needs_input" else 2
    try:
        job = run_pipeline_job(result.request, args.output_root, ai_proposal={**result.metadata(), "mode": adapter.mode})
    except PipelineJobError as error:
        _emit({**summary, "status": "rejected", "error": error.code})
        return 2
    _emit({**summary, "job": job.to_dict()})
    return 0 if job.status == "accepted" else 2


def _run_execution(args: argparse.Namespace) -> int:
    from .execute import load_rows, run_pipeline_execution_job

    try:
        request = PipelineRequest.model_validate_json(args.input.read_bytes()[: MAX_PIPELINE_REQUEST_BYTES + 1])
        rows = load_rows(args.rows)
    except (OSError, ValueError, ValidationError):
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2
    try:
        result = run_pipeline_execution_job(request, rows, args.output_root)
    except PipelineJobError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    _emit(result.to_dict())
    return 0 if result.status == "accepted" else 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.pipeline_command == "brief-shadow":
        return _run_shadow(args)
    if args.pipeline_command == "propose":
        return _run_propose(args)
    if args.pipeline_command == "run":
        return _run_execution(args)
    try:
        if args.input.is_symlink():
            raise PipelineInputError("cli_io_error")
        with args.input.open("rb") as source:
            body = source.read(MAX_PIPELINE_REQUEST_BYTES + 1)
    except PipelineInputError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    except OSError:
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2

    if len(body) > MAX_PIPELINE_REQUEST_BYTES:
        _emit({"status": "rejected", "error": "pipeline_request_too_large"})
        return 2
    try:
        request = PipelineRequest.model_validate_json(body)
    except ValidationError:
        _emit({"status": "rejected", "error": "invalid_pipeline_request"})
        return 2

    try:
        result = run_pipeline_job(request, args.output_root)
    except PipelineJobError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    _emit(result.to_dict())
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
