"""The deterministic monthly report pipeline runner (T6.3).

Runs one bounded chain over the existing local CLIs:

1. ``data_pipeline`` — ``data_production.cli build`` cleans the configured
   CSV (no chart).
2. ``data_viz`` — ``data_production.cli build`` builds the chart over the
   stage-1 cleaned CSV artifact.
3. ``spreadsheet`` — ``book_production_platform.cli spreadsheet build``
   builds the workbook from the cleaned CSV and typed columns.
4. ``documents`` — ``document_production.cli report`` renders the report
   DOCX/PDF referencing the chart, the workbook, and the totals.

Every boundary is a versioned contract validated before and after each
stage; the run publishes atomically (staging directory renamed into place
only after the validated ``pipeline-report.v1`` manifest is written). A
failure at any stage fails closed: nothing partial is published and the
staging directory is removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from .contracts import (
    CONTRACT_DATA_JOB,
    CONTRACT_DATASET_REQUEST,
    CONTRACT_DOCUMENT_MANIFEST,
    CONTRACT_MANIFEST,
    CONTRACT_REPORT_REQUEST,
    CONTRACT_SPREADSHEET_JOB,
    ContractError,
    PipelineReportConfig,
    check_relative_path,
    parse_config_file,
    parse_manifest,
)
from .paths import (
    DATA_IR_ROOT,
    DATA_SERVICE_ROOT,
    DOCUMENTS_IR_ROOT,
    DOCUMENTS_SERVICE_ROOT,
    NESTED_PLATFORM_SRC,
)
from .stage_requests import (
    build_data_stage_request,
    build_document_stage_request,
    build_viz_stage_request,
    build_workbook_stage_request,
)
from .stages import (
    CompletedStage,
    StageCommand,
    StageFailure,
    StageInvoker,
    SubprocessInvoker,
    build_stage_env,
    parse_stage_json,
    verify_stage_result,
)

PIPELINE_MANIFEST_NAME = "pipeline-manifest.json"

EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_STAGE_FAILED = 3
EXIT_CONTRACT_VIOLATION = 4
EXIT_OUTPUT_EXISTS = 5

EXIT_CODES = {
    "ok": EXIT_OK,
    "input_error": EXIT_INPUT_ERROR,
    "stage_failed": EXIT_STAGE_FAILED,
    "contract_violation": EXIT_CONTRACT_VIOLATION,
    "output_exists": EXIT_OUTPUT_EXISTS,
}

STAGE_DIRS = {
    "data_pipeline": "stages/01-data-pipeline",
    "data_viz": "stages/02-data-viz",
    "spreadsheet": "stages/03-spreadsheet",
    "documents": "stages/04-document",
}

DATA_TOOL = "services/data-production-api: python -m data_production.cli build"
SPREADSHEET_TOOL = "book-production-platform: python -m book_production_platform.cli spreadsheet build"
DOCUMENTS_TOOL = "services/document-production-api: python -m document_production.cli report"


class RunResult:
    """Outcome of one pipeline run."""

    def __init__(self, exit_code: int, manifest_path: Path | None, error: dict[str, str] | None) -> None:
        self.exit_code = exit_code
        self.manifest_path = manifest_path
        self.error = error


@dataclass(frozen=True)
class _StageOutcome:
    stage: str
    argv: list[str]
    manifest_relative: str
    job_id: str
    artifacts: list[dict[str, Any]]


def _config_hash(config: PipelineReportConfig) -> str:
    payload = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_request(staging: Path, stage: str, payload: dict[str, Any]) -> str:
    relative = f"{STAGE_DIRS[stage]}/request.json"
    path = staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return relative


def _artifact_record(job_root: Path, run_root: Path, artifact: Any) -> dict[str, Any]:
    relative = check_relative_path((job_root / artifact.filename).relative_to(run_root).as_posix())
    return {
        "name": artifact.filename,
        "path": relative,
        "media_type": artifact.media_type,
        "content_hash": artifact.content_hash,
        "byte_size": artifact.byte_size,
    }


def _ensure_stage_success(stage: str, completed: CompletedStage) -> None:
    """Raise the stage's stable rejection code when the CLI did not accept."""

    if completed.returncode == 0:
        return
    detail = completed.stderr.strip()[:500] or "stage CLI returned a non-zero exit code"
    code = f"stage_exit_{completed.returncode}"
    try:
        payload = parse_stage_json(stage, completed.stdout)
    except StageFailure:
        payload = None
    if isinstance(payload, dict) and payload.get("status") not in (None, "accepted"):
        code = str(payload.get("error_code") or code)
    raise StageFailure(stage, code, detail)


def _finish_batch_stage(stage: str, argv: list[str], completed: CompletedStage, stage_root: Path, run_root: Path) -> _StageOutcome:
    """Finish a data/spreadsheet stage whose job directory is ``job-<hash>``."""

    _ensure_stage_success(stage, completed)
    job_root = _find_job_dir(stage_root, "job-")
    parsed = parse_stage_json(stage, completed.stdout)
    manifest = verify_stage_result(stage, job_root, parsed)
    artifacts = [_artifact_record(job_root, run_root, artifact) for artifact in manifest.all_artifacts()]
    job_id = manifest.job_id or f"{stage}-job"
    manifest_relative = check_relative_path((job_root / "manifest.json").relative_to(run_root).as_posix())
    return _StageOutcome(stage, argv, manifest_relative, job_id, artifacts)


def _finish_document_stage(stage: str, argv: list[str], completed: CompletedStage, stage_root: Path, run_root: Path) -> _StageOutcome:
    """Finish the documents stage whose job directory is ``jobs/<uuid>``."""

    _ensure_stage_success(stage, completed)
    parsed = parse_stage_json(stage, completed.stdout)
    job_root = stage_root / "jobs" / str(parsed.get("job_id", ""))
    if not job_root.is_dir():
        raise StageFailure(stage, "artifact_integrity_failed", "documents job directory is missing")
    manifest = verify_stage_result(stage, job_root, parsed)
    artifacts = [_artifact_record(job_root, run_root, artifact) for artifact in manifest.all_artifacts()]
    job_id = manifest.job_id or f"{stage}-job"
    manifest_relative = check_relative_path((job_root / "manifest.json").relative_to(run_root).as_posix())
    return _StageOutcome(stage, argv, manifest_relative, job_id, artifacts)


def _find_job_dir(stage_root: Path, prefix: str) -> Path:
    candidates = sorted(path for path in stage_root.iterdir() if path.is_dir() and path.name.startswith(prefix))
    if len(candidates) != 1:
        raise StageFailure("pipeline", "contract_violation", f"expected exactly one {prefix}* job directory")
    return candidates[0]


def _stage_record(outcome: _StageOutcome, input_contract: str, output_contract: str, tool: str) -> dict[str, Any]:
    # The manifest never records absolute local paths; the interpreter token
    # is normalized so the recorded command is a portable, relative shape.
    command = ["python" if index == 0 and arg == sys.executable else arg for index, arg in enumerate(outcome.argv)]
    return {
        "stage": outcome.stage,
        "status": "accepted",
        "tool": tool,
        "command": command,
        "input_contract": input_contract,
        "output_contract": output_contract,
        "request_path": f"{STAGE_DIRS[outcome.stage]}/request.json",
        "manifest_path": outcome.manifest_relative,
        "job_id": outcome.job_id,
        "artifacts": outcome.artifacts,
    }


def _read_text_artifact(stage: str, run_root: Path, artifacts: list[dict[str, Any]], name: str) -> str:
    record = next((item for item in artifacts if item["name"] == name), None)
    if record is None:
        raise StageFailure(stage, "artifact_integrity_failed", f"expected artifact {name} in stage output")
    try:
        return cast(str, (run_root / record["path"]).read_text(encoding="utf-8"))
    except OSError as exc:
        raise StageFailure(stage, "artifact_unreadable", f"cannot read {name}: {exc}") from exc


def _artifact_record_named(stage: str, artifacts: list[dict[str, Any]], name: str) -> dict[str, Any]:
    record = next((item for item in artifacts if item["name"] == name), None)
    if record is None:
        raise StageFailure(stage, "artifact_integrity_failed", f"expected artifact {name} in stage output")
    return record


def _require_aggregate_total(stage: str, chart_block: dict[str, Any]) -> str:
    value = chart_block.get("aggregate_total")
    if not isinstance(value, str):
        raise StageFailure(stage, "contract_violation", "aggregate_total must be a string")
    try:
        return str(Decimal(value))
    except InvalidOperation as exc:
        raise StageFailure(stage, "contract_violation", f"aggregate_total is not decimal: {value!r}") from exc


def _run_data_viz(
    config: PipelineReportConfig,
    staging: Path,
    invoker: StageInvoker,
    data_env: dict[str, str],
    cleaned_csv_text: str,
    records: list[dict[str, Any]],
) -> tuple[_StageOutcome, dict[str, Any]]:
    viz_request_relative = _write_request(
        staging,
        "data_viz",
        build_viz_stage_request(config, cleaned_csv_text, f"pipeline:{config.pipeline_id}:stage:data_pipeline:cleaned.csv"),
    )
    viz_argv = [
        sys.executable,
        "-m",
        "data_production.cli",
        "build",
        "--input", viz_request_relative,
        "--output", STAGE_DIRS["data_viz"],
        "--idempotency-key", f"{config.pipeline_id}-02-viz",
    ]
    viz_root = staging / STAGE_DIRS["data_viz"]
    viz_root.mkdir(parents=True, exist_ok=True)
    command = StageCommand("data_viz", viz_argv, staging, data_env, float(config.stage_timeout_seconds))
    viz_completed = invoker(command)
    viz_stage = _finish_batch_stage("data_viz", viz_argv, viz_completed, viz_root, staging)
    records.append(_stage_record(viz_stage, CONTRACT_DATASET_REQUEST, CONTRACT_DATA_JOB, DATA_TOOL))

    viz_report_text = _read_text_artifact("data_viz", staging, viz_stage.artifacts, "report.json")
    try:
        viz_report = json.loads(viz_report_text)
    except json.JSONDecodeError as exc:
        raise StageFailure("data_viz", "contract_violation", f"viz report.json is not valid JSON: {exc}") from exc
    chart_block = viz_report.get("chart") if isinstance(viz_report, dict) else None
    if not isinstance(chart_block, dict):
        raise StageFailure("data_viz", "contract_violation", "viz report.json has no chart block")
    _require_aggregate_total("data_viz", chart_block)
    return viz_stage, viz_report


def run_monthly_report(
    config_path: str | Path,
    output: str | Path,
    *,
    invoker: StageInvoker | None = None,
) -> RunResult:
    """Run the full monthly report chain and publish it atomically.

    Exit codes: ``0`` published, ``2`` config or input error, ``3`` a stage
    CLI failed, ``4`` a boundary contract violation, ``5`` the output
    directory already exists. Nothing is published on failure.
    """

    active_invoker = invoker if invoker is not None else SubprocessInvoker()
    output_root = Path(output)

    if not Path(config_path).is_file():
        return RunResult(EXIT_INPUT_ERROR, None, {"error": "config_not_found", "detail": str(config_path)})
    if output_root.exists():
        return RunResult(EXIT_OUTPUT_EXISTS, None, {"error": "output_exists", "detail": str(output_root)})

    try:
        config = parse_config_file(config_path)
    except ValueError as exc:
        return RunResult(EXIT_INPUT_ERROR, None, {"error": "invalid_config", "detail": str(exc)})

    missing = [
        str(path)
        for path in (DATA_SERVICE_ROOT, DATA_IR_ROOT, DOCUMENTS_SERVICE_ROOT, DOCUMENTS_IR_ROOT, NESTED_PLATFORM_SRC)
        if not path.is_dir()
    ]
    if missing:
        return RunResult(
            EXIT_STAGE_FAILED,
            None,
            {"error": "stage_unavailable", "stage": "pipeline", "detail": f"missing stage roots: {', '.join(missing)}"},
        )

    staging = output_root.parent / f".{output_root.name}.staging-{uuid.uuid4().hex[:12]}"
    records: list[dict[str, Any]] = []
    try:
        staging.mkdir(parents=True)
        data_env = build_stage_env([str(DATA_SERVICE_ROOT), str(DATA_IR_ROOT)])

        # --- Stage 1: data pipeline (cleaning only) -----------------------
        data_request_relative = _write_request(staging, "data_pipeline", build_data_stage_request(config))
        data_argv = [
            sys.executable,
            "-m",
            "data_production.cli",
            "build",
            "--input", data_request_relative,
            "--output", STAGE_DIRS["data_pipeline"],
            "--idempotency-key", f"{config.pipeline_id}-01-data",
        ]
        data_root = staging / STAGE_DIRS["data_pipeline"]
        data_root.mkdir(parents=True, exist_ok=True)
        data_command = StageCommand("data_pipeline", data_argv, staging, data_env, float(config.stage_timeout_seconds))
        data_completed = active_invoker(data_command)
        data_stage = _finish_batch_stage("data_pipeline", data_argv, data_completed, data_root, staging)
        records.append(_stage_record(data_stage, CONTRACT_DATASET_REQUEST, CONTRACT_DATA_JOB, DATA_TOOL))
        cleaned_csv_text = _read_text_artifact("data_pipeline", staging, data_stage.artifacts, "cleaned.csv")

        # --- Stage 2: data viz (chart over the cleaned artifact) ----------
        viz_stage, viz_report = _run_data_viz(config, staging, active_invoker, data_env, cleaned_csv_text, records)
        chart_block = viz_report["chart"]
        _require_aggregate_total("data_viz", chart_block)

        # --- Stage 3: spreadsheet workbook --------------------------------
        data_report_text = _read_text_artifact("data_pipeline", staging, data_stage.artifacts, "report.json")
        try:
            data_report = json.loads(data_report_text)
        except json.JSONDecodeError as exc:
            raise StageFailure("data_pipeline", "contract_violation", f"data report.json is not valid JSON: {exc}") from exc
        columns = data_report.get("columns") if isinstance(data_report, dict) else None
        if not isinstance(columns, list) or not columns:
            raise StageFailure("data_pipeline", "contract_violation", "data report.json has no columns")
        workbook_request = build_workbook_stage_request(
            config,
            columns,
            cleaned_csv_text,
            f"pipeline:{config.pipeline_id}:stage:data_pipeline:cleaned.csv",
        )
        workbook_request_relative = _write_request(staging, "spreadsheet", workbook_request)
        workbook_argv = [
            sys.executable,
            "-m",
            "book_production_platform.cli",
            "spreadsheet",
            "build",
            "--input", workbook_request_relative,
            "--output", STAGE_DIRS["spreadsheet"],
            "--idempotency-key", f"{config.pipeline_id}-03-workbook",
        ]
        workbook_env = build_stage_env([str(NESTED_PLATFORM_SRC)])
        workbook_root = staging / STAGE_DIRS["spreadsheet"]
        workbook_root.mkdir(parents=True, exist_ok=True)
        workbook_command = StageCommand("spreadsheet", workbook_argv, staging, workbook_env, float(config.stage_timeout_seconds))
        workbook_completed = active_invoker(workbook_command)
        workbook_stage = _finish_batch_stage("spreadsheet", workbook_argv, workbook_completed, workbook_root, staging)
        records.append(_stage_record(workbook_stage, CONTRACT_SPREADSHEET_JOB, CONTRACT_SPREADSHEET_JOB, SPREADSHEET_TOOL))
        workbook_record = _artifact_record_named("spreadsheet", workbook_stage.artifacts, "workbook.xlsx")

        # --- Stage 4: documents report ------------------------------------
        chart_record = _artifact_record_named("data_viz", viz_stage.artifacts, "chart.svg")
        document_request = build_document_stage_request(
            config,
            viz_report,
            workbook_name=str(workbook_record["name"]),
            workbook_media_type=str(workbook_record["media_type"]),
            workbook_hash=str(workbook_record["content_hash"]),
            workbook_size=int(workbook_record["byte_size"]),
            chart_name=str(chart_record["name"]),
            chart_media_type=str(chart_record["media_type"]),
            chart_hash=str(chart_record["content_hash"]),
            chart_size=int(chart_record["byte_size"]),
        )
        document_request_relative = _write_request(staging, "documents", document_request)
        document_argv = [
            sys.executable,
            "-m",
            "document_production.cli",
            "report",
            "--input", document_request_relative,
            "--output", STAGE_DIRS["documents"],
            "--idempotency-key", f"{config.pipeline_id}-04-report",
        ]
        document_env = build_stage_env([str(DOCUMENTS_SERVICE_ROOT), str(DOCUMENTS_IR_ROOT)])
        document_root = staging / STAGE_DIRS["documents"]
        document_root.mkdir(parents=True, exist_ok=True)
        document_command = StageCommand("documents", document_argv, staging, document_env, float(config.stage_timeout_seconds))
        document_completed = active_invoker(document_command)
        document_stage = _finish_document_stage("documents", document_argv, document_completed, document_root, staging)
        records.append(_stage_record(document_stage, CONTRACT_REPORT_REQUEST, CONTRACT_DOCUMENT_MANIFEST, DOCUMENTS_TOOL))

        # --- Publish the validated pipeline manifest atomically -----------
        manifest_payload = {
            "schema_version": CONTRACT_MANIFEST,
            "pipeline_id": config.pipeline_id,
            "config_hash": _config_hash(config),
            "model_calls": 0,
            "stages": records,
        }
        parse_manifest(manifest_payload)  # fail closed before publishing
        if output_root.exists():
            raise FileExistsError(str(output_root))
        manifest_path = staging / PIPELINE_MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        os.rename(staging, output_root)
        return RunResult(EXIT_OK, output_root / PIPELINE_MANIFEST_NAME, None)
    except StageFailure as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return RunResult(EXIT_STAGE_FAILED, None, {"error": exc.code, "stage": exc.stage, "detail": exc.detail})
    except ContractError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return RunResult(EXIT_CONTRACT_VIOLATION, None, {"error": "contract_violation", "contract": exc.contract, "detail": str(exc)})
    except FileExistsError:
        shutil.rmtree(staging, ignore_errors=True)
        return RunResult(EXIT_OUTPUT_EXISTS, None, {"error": "output_exists", "detail": str(output_root)})
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return RunResult(EXIT_STAGE_FAILED, None, {"error": "pipeline_io_error", "stage": "pipeline", "detail": str(exc)})
    except (ValueError, KeyError, TypeError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return RunResult(EXIT_CONTRACT_VIOLATION, None, {"error": "pipeline_contract_error", "stage": "pipeline", "detail": str(exc)})


__all__ = ["EXIT_CODES", "PIPELINE_MANIFEST_NAME", "RunResult", "run_monthly_report"]
