"""Opt-in local execution of a compiled pipeline over caller-supplied rows, with CSV export.

V1A production stays plan-only against real sources: this module never
connects to a database. A caller who already holds the source rows (for
example an exported CSV/JSON of the table) can run the *same* compiled,
parameterized ``SELECT`` read-only (``PRAGMA query_only``) in an in-memory
SQLite table and get ``results.csv`` plus a metadata manifest. The plan job
(:func:`.jobs.run_pipeline_job`) runs first, so the published plan artifacts
and the executed SQL are identical.

Results and the manual guide contain data values and stay in the private
output root (0700 directories, 0600 files). The trace and history hold only
hashes, counts, and column names.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline_ir.models import (
    ColumnSpec,
    PipelineIR,
    PipelineRequest,
)
from book_production_platform.data_pipeline_ir.validation import build_pipeline_ir
from book_production_platform.production_trace import TraceRecorder, sha256_json

from .jobs import (
    ENGINE_VERSION,
    RECIPE_VERSION,
    TOOL_ID,
    PipelineJobError,
    _ensure_directory,
    _idempotency_lock,
    _private_write,
    _restrict_trace_files,
    run_pipeline_job,
)
from .manual import build_power_query_guide
from .pipeline import (
    CompiledPipeline,
    _quote_identifier,
    _sqlite_parameter,
    _sqlite_type,
    compile_pipeline_ir,
)

EXECUTION_SCHEMA = "pipeline-execution.v1"
RESULTS_FILENAME = "results.csv"
EXECUTION_MANIFEST = "execution-manifest.json"
MAX_EXECUTION_ROWS = 50_000
_CSV_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")
_INTEGER_TEXT = re.compile(r"^-?\d{1,18}$")


class PipelineExecutionError(ValueError):
    """A stable, value-free execution failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    ordered: bool
    csv_bytes: bytes
    values_sha256: str
    numeric_totals: dict[str, str] = field(default_factory=dict)
    csv_guarded_cells: int = 0

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def summary(self) -> dict[str, Any]:
        """Check values for the manual guide (contains data: first row, totals)."""

        return {
            "row_count": self.row_count,
            "columns": list(self.columns),
            "numeric_totals": dict(self.numeric_totals),
            "first_row": dict(zip(self.columns, (_text(value) for value in self.rows[0]), strict=True)) if self.rows else {},
            "csv_sha256": hashlib.sha256(self.csv_bytes).hexdigest(),
            "values_sha256": self.values_sha256,
        }


@dataclass(frozen=True)
class ExecutionJobResult:
    status: str
    run_id: str | None
    fingerprint: str | None
    result_dir: Path | None
    row_count: int
    artifact_hashes: dict[str, str]
    replay: bool
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        """Metadata only; never row values."""

        return {
            "status": self.status,
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
            "result_dir": str(self.result_dir) if self.result_dir is not None else None,
            "row_count": self.row_count,
            "artifact_hashes": dict(self.artifact_hashes),
            "replay": self.replay,
            "error_code": self.error_code,
            "model_calls": 0,
        }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = format(Decimal(repr(value)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def _bind(value: Any, column: ColumnSpec) -> Any:
    """Coerce one supplied cell to the declared source type (raises without echoing the value)."""

    if value is None or (value == "" and column.data_type != "text"):
        return None
    data_type = column.data_type
    if data_type == "text":
        if not isinstance(value, str):
            raise PipelineExecutionError("execution_rows_invalid")
        return value
    if data_type == "boolean":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return int(value.strip().lower() == "true")
        raise PipelineExecutionError("execution_rows_invalid")
    if isinstance(value, bool):
        raise PipelineExecutionError("execution_rows_invalid")
    if data_type == "integer":
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
            return int(value)
        raise PipelineExecutionError("execution_rows_invalid")
    if data_type == "decimal":
        if not isinstance(value, (int, float, str, Decimal)):
            raise PipelineExecutionError("execution_rows_invalid")
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise PipelineExecutionError("execution_rows_invalid") from None
        if not number.is_finite():
            raise PipelineExecutionError("execution_rows_invalid")
        return format(number, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise PipelineExecutionError("execution_rows_invalid") from None
        if parsed.isoformat() == value:
            return value
    raise PipelineExecutionError("execution_rows_invalid")


def _typed_output(value: Any, column: ColumnSpec) -> Any:
    if value is None:
        return None
    if column.data_type == "boolean":
        return bool(value)
    if column.data_type == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def normalize_rows(pipeline: PipelineIR, rows: Any) -> list[list[Any]]:
    """Validate rows keyed by column key (or declared ``source_name``) and bind them per type."""

    if not isinstance(rows, list) or len(rows) > MAX_EXECUTION_ROWS or not all(isinstance(row, Mapping) for row in rows):
        raise PipelineExecutionError("execution_rows_invalid")
    columns = pipeline.source.columns
    names = {column.key for column in columns} | {column.source_name for column in columns if column.source_name}
    bound: list[list[Any]] = []
    for row in rows:
        if set(row) - names:
            raise PipelineExecutionError("execution_rows_invalid")
        bound.append([_bind(row[column.key] if column.key in row else row.get(column.source_name or column.key), column) for column in columns])
    return bound


def _csv_cell(value: Any) -> tuple[str, bool]:
    text = _text(value)
    if isinstance(value, str) and text.startswith(_CSV_FORMULA_PREFIX):
        return "'" + text, True  # keep spreadsheet apps from executing exported text as a formula
    return text, False


def execute_pipeline(pipeline: PipelineIR, compiled: CompiledPipeline, bound_rows: list[list[Any]]) -> ExecutionResult:
    """Run the compiled SELECT read-only over the bound rows in an in-memory SQLite database."""

    connection = sqlite3.connect(":memory:")
    try:
        columns = pipeline.source.columns
        table = _quote_identifier(pipeline.source.table_name)
        connection.execute(f"CREATE TABLE {table} (" + ", ".join(f"{_quote_identifier(column.key)} {_sqlite_type(column.data_type)}" for column in columns) + ")")
        connection.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})", bound_rows)
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(compiled.sql, [_sqlite_parameter(value) for value in compiled.parameters])
        raw_rows = cursor.fetchall()
    except sqlite3.Error:
        raise PipelineExecutionError("pipeline_execution_failed") from None
    finally:
        connection.close()
    output_columns = pipeline.output_columns
    rows = [tuple(_typed_output(value, column) for value, column in zip(row, output_columns, strict=True)) for row in raw_rows]
    output = next(node for node in pipeline.ordered_nodes if node.node_id == pipeline.output_node)
    ordered = output.op in {"sort_by", "limit"}
    canonical = rows if ordered else sorted(rows, key=lambda row: [(value is None, _text(value)) for value in row])
    values_sha256 = sha256_json({"columns": [column.key for column in output_columns], "ordered": ordered, "rows": [[_text(value) for value in row] for row in canonical]})
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([column.key for column in output_columns])
    guarded = 0
    for row in rows:
        cells = [_csv_cell(value) for value in row]
        guarded += sum(1 for _, flag in cells if flag)
        writer.writerow([text for text, _ in cells])
    totals: dict[str, str] = {}
    for index, column in enumerate(output_columns):
        if column.data_type in {"integer", "decimal"}:
            total = sum((Decimal(_text(row[index])) for row in rows if row[index] is not None), Decimal(0))
            text = format(total, "f")
            totals[column.key] = text.rstrip("0").rstrip(".") if "." in text else text
    return ExecutionResult(
        columns=[column.key for column in output_columns], rows=rows, ordered=ordered, csv_bytes=buffer.getvalue().encode("utf-8"),
        values_sha256=values_sha256, numeric_totals=totals, csv_guarded_cells=guarded,
    )


def _rejected(code: str, fingerprint: str | None = None) -> ExecutionJobResult:
    return ExecutionJobResult("rejected", None, fingerprint, None, 0, {}, False, code)


def _trace_execution_failure(trace: TraceRecorder, root: Path, code: str) -> None:
    try:
        _ensure_directory(root / "traces")
        trace_dir = root / "traces" / f"run-{trace.job_id}"
        _ensure_directory(trace_dir)
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=trace_dir, output_root=root)
    except (OSError, PipelineJobError):
        pass


def run_pipeline_execution_job(request: PipelineRequest | dict[str, Any], rows: Any, output_root: str | Path) -> ExecutionJobResult:
    """Publish the plan, then execute it read-only over ``rows`` and export ``results.csv``.

    Content-addressed by (plan fingerprint, rows hash): a repeat run with the
    same plan and rows replays after re-hashing the stored results.
    """

    plan = run_pipeline_job(request, output_root)
    if plan.status != "accepted" or plan.fingerprint is None or plan.artifact_dir is None:
        return _rejected(plan.error_code or "pipeline_rejected")
    root = plan.artifact_dir.parent.parent
    pipeline = build_pipeline_ir(request)
    compiled = compile_pipeline_ir(pipeline)
    rows_sha256 = sha256_json(rows)
    run_id = hashlib.sha256(f"{plan.fingerprint}:{rows_sha256}".encode()).hexdigest()[:32]
    trace = TraceRecorder(tool_id=TOOL_ID, job_id=run_id, engine_version=ENGINE_VERSION, recipe_version=RECIPE_VERSION, schema_version=EXECUTION_SCHEMA)
    trace.input_json("request", {"fingerprint": plan.fingerprint})
    trace.inputs.append({"name": "rows", "role": "source", "sha256": rows_sha256, "rows": len(rows) if isinstance(rows, list) else None,
                         "columns": len(pipeline.source.columns)})
    trace.record_step("plan", "run_pipeline_job", fingerprint=plan.fingerprint, replay=plan.replay)
    result_dir = root / "results" / run_id
    with _idempotency_lock(root / ".locks" / f"run-{run_id}.lock"):
        manifest_path = result_dir / EXECUTION_MANIFEST
        if manifest_path.is_file() and not result_dir.is_symlink():
            return _replay(result_dir, run_id, plan.fingerprint)
        try:
            with trace.step("load", "bind_rows", columns=[column.key for column in pipeline.source.columns]) as load_step:
                bound = normalize_rows(pipeline, rows)
                load_step.params["rows"] = len(bound)
            with trace.step("execute", "sqlite_query_only", sql_sha256=hashlib.sha256(compiled.sql.encode()).hexdigest(),
                            parameters=len(compiled.parameters)) as run_step:
                result = execute_pipeline(pipeline, compiled, bound)
                run_step.params.update({"output_rows": result.row_count, "ordered": result.ordered})
        except PipelineExecutionError as error:
            _trace_execution_failure(trace, root, error.code)
            return _rejected(error.code, plan.fingerprint)
        manifest = {
            "schema_version": EXECUTION_SCHEMA,
            "run_id": run_id,
            "fingerprint": plan.fingerprint,
            "rows_sha256": rows_sha256,
            "row_count": result.row_count,
            "columns": result.columns,
            "ordered": result.ordered,
            "values_sha256": result.values_sha256,
            "csv_sha256": hashlib.sha256(result.csv_bytes).hexdigest(),
            "csv_guarded_cells": result.csv_guarded_cells,
            "model_calls": 0,
        }
        _ensure_directory(root / "results")
        _ensure_directory(result_dir)
        with trace.step("export", "write_csv", columns=result.columns, guarded_cells=result.csv_guarded_cells) as export_step:
            _private_write(result_dir / RESULTS_FILENAME, result.csv_bytes)
            _private_write(manifest_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
            export_step.outputs = [RESULTS_FILENAME, EXECUTION_MANIFEST]
        ir_payload = pipeline.model_dump(mode="json")
        _private_write(result_dir / "ir.json", json.dumps(ir_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        trace.ir_document(ir_payload, schema=pipeline.schema_version, path=result_dir / "ir.json")
        trace.artifact(result_dir / RESULTS_FILENAME, media_type="text/csv")
        trace.artifact(result_dir / EXECUTION_MANIFEST, media_type="application/json")
        trace.finish(status="accepted", job_dir=result_dir, output_root=root, guide=build_power_query_guide(pipeline, compiled, execution=result.summary()))
        _restrict_trace_files(result_dir)
        hashes = {name: hashlib.sha256((result_dir / name).read_bytes()).hexdigest() for name in (RESULTS_FILENAME, EXECUTION_MANIFEST)}
        return ExecutionJobResult("accepted", run_id, plan.fingerprint, result_dir, result.row_count, hashes, False, None)


def _replay(result_dir: Path, run_id: str, fingerprint: str) -> ExecutionJobResult:
    try:
        manifest = json.loads((result_dir / EXECUTION_MANIFEST).read_text(encoding="utf-8"))
        csv_bytes = (result_dir / RESULTS_FILENAME).read_bytes()
    except (OSError, ValueError):
        raise PipelineJobError("replay_integrity_error") from None
    if (
        manifest.get("schema_version") != EXECUTION_SCHEMA or manifest.get("run_id") != run_id or manifest.get("fingerprint") != fingerprint
        or manifest.get("csv_sha256") != hashlib.sha256(csv_bytes).hexdigest()
    ):
        raise PipelineJobError("replay_integrity_error")
    hashes = {name: hashlib.sha256((result_dir / name).read_bytes()).hexdigest() for name in (RESULTS_FILENAME, EXECUTION_MANIFEST)}
    return ExecutionJobResult("accepted", run_id, fingerprint, result_dir, int(manifest.get("row_count", 0)), hashes, True, None)


def load_rows(path: str | Path) -> Any:
    """Read a rows document: a JSON array of objects, or ``{"rows": [...]}``."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("rows") if isinstance(data, dict) else data


__all__ = [
    "EXECUTION_MANIFEST",
    "EXECUTION_SCHEMA",
    "MAX_EXECUTION_ROWS",
    "RESULTS_FILENAME",
    "ExecutionJobResult",
    "ExecutionResult",
    "PipelineExecutionError",
    "execute_pipeline",
    "load_rows",
    "normalize_rows",
    "run_pipeline_execution_job",
]
