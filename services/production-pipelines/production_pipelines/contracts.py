"""The versioned pipeline contracts and their validators.

Owned contracts (JSON Schemas published under ``schemas/``):

- ``pipeline-report-config.v1``: the monthly report job configuration.
- ``pipeline-report.v1``: the pipeline manifest linking stage job and
  artifact identities.

Consumer-side validators (the upstream services own the contracts; copies
here only validate what this orchestrator emits or consumes, mirroring the
T6.2 precedent in ``services/video-postproduction``):

- ``dataset-request.v1`` for the data and data-viz stage requests.
- ``workbook-request.v1`` for the spreadsheet stage request.
- ``report-document-request.v1`` kept aligned with
  ``services/document-production-api/schemas/report-document-request.v1.schema.json``
  by a dedicated test.

Published artifacts and manifests contain relative paths only; the typed
models reject absolute local paths, drive letters, and backslashes.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .paths import SCHEMAS_DIR

CONTRACT_CONFIG = "pipeline-report-config.v1"
CONTRACT_MANIFEST = "pipeline-report.v1"
CONTRACT_DATASET_REQUEST = "dataset-request.v1"
CONTRACT_WORKBOOK_REQUEST = "workbook-request.v1"
CONTRACT_REPORT_REQUEST = "report-document-request.v1"
CONTRACT_DATA_JOB = "data-job.v1"
CONTRACT_SPREADSHEET_JOB = "spreadsheet-job.v1"
CONTRACT_DOCUMENT_MANIFEST = "document-production-manifest"

MAX_CSV_TEXT_BYTES = 2_000_000
MAX_STAGE_TIMEOUT_SECONDS = 900
MIN_STAGE_TIMEOUT_SECONDS = 30

SCHEMA_FILES: dict[str, str] = {
    CONTRACT_CONFIG: "pipeline-report-config.v1.schema.json",
    CONTRACT_MANIFEST: "pipeline-report.v1.schema.json",
    CONTRACT_DATASET_REQUEST: "dataset-request.v1.schema.json",
    CONTRACT_WORKBOOK_REQUEST: "workbook-request.v1.schema.json",
    CONTRACT_REPORT_REQUEST: "report-document-request.v1.schema.json",
}

_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_ASCII_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_PIPELINE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ContractError(ValueError):
    """Raised when a payload violates one of the pipeline contracts."""

    def __init__(self, contract: str, issues: list[str]) -> None:
        self.contract = contract
        self.issues = issues
        super().__init__(f"{contract}: " + "; ".join(issues[:5]))


def load_schema(contract: str) -> dict[str, Any]:
    """Load the published JSON Schema file for a contract."""

    try:
        filename = SCHEMA_FILES[contract]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ContractError(contract, [f"unknown contract: {contract}"]) from exc
    return cast(dict[str, Any], json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8")))


def schema_issues(contract: str, payload: Any) -> list[str]:
    """Return sorted JSON Schema error messages for the payload."""

    validator = Draft202012Validator(load_schema(contract))
    messages: list[str] = []
    for error in validator.iter_errors(payload):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        messages.append(f"{path}: {error.message}")
    return sorted(messages)


def validate_config_payload(payload: Any) -> list[str]:
    """Structural ``pipeline-report-config.v1`` violations (empty means valid)."""

    return schema_issues(CONTRACT_CONFIG, payload)


def validate_manifest_payload(payload: Any) -> list[str]:
    """Structural ``pipeline-report.v1`` violations (empty means valid)."""

    return schema_issues(CONTRACT_MANIFEST, payload)


def check_relative_path(value: str) -> str:
    """Normalize a path to posix form and reject absolute local paths."""

    normalized = Path(value).as_posix()
    if not _RELATIVE_PATH.fullmatch(normalized):
        raise ContractError("relative-path", [f"path must be relative and plain: {value!r}"])
    return normalized


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ColumnSpecConfig(_StrictModel):
    """Typed column definition mirroring the upstream ``ColumnSpec``."""

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    data_type: Literal["text", "integer", "decimal", "date", "boolean"]
    required: bool = False
    source_name: str | None = Field(default=None, min_length=1, max_length=200)


class TransformSpecConfig(_StrictModel):
    """Allowed cleaning transform mirroring the upstream ``TransformSpec``."""

    op: Literal["trim_text", "fill_null", "filter_equals", "sort_by"]
    column: str | None = Field(default=None, min_length=1, max_length=100)
    value: Any = None
    direction: Literal["asc", "desc"] = "asc"


class ChartConfig(_StrictModel):
    """Chart specification mirroring the upstream ``ChartSpec``."""

    chart_type: Literal["bar", "line"] = "bar"
    x_column: str = Field(min_length=1, max_length=100)
    y_column: str | None = Field(default=None, min_length=1, max_length=100)
    aggregation: Literal["sum", "count", "avg"] = "count"
    title: str = Field(min_length=1, max_length=200)
    x_axis_label: str | None = Field(default=None, min_length=1, max_length=120)
    y_axis_label: str | None = Field(default=None, min_length=1, max_length=120)
    value_unit: str | None = Field(default=None, min_length=1, max_length=32)
    width: int = Field(default=720, ge=240, le=4096)
    height: int = Field(default=420, ge=180, le=4096)


class DatasetConfig(_StrictModel):
    """Source dataset for the data pipeline stage (CSV inline, bounded)."""

    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    title: str = Field(min_length=1, max_length=200)
    delimiter: Literal[",", ";", "\t"] = ","
    columns: list[ColumnSpecConfig] = Field(min_length=1, max_length=100)
    transforms: list[TransformSpecConfig] = Field(default_factory=list, max_length=20)
    csv_text: str = Field(min_length=1, max_length=MAX_CSV_TEXT_BYTES)
    source_ref: str = Field(default="input.csv", min_length=1, max_length=200)


class WorkbookConfig(_StrictModel):
    """Spreadsheet stage parameters; column keys map data keys to ASCII keys."""

    workbook_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    column_keys: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def _check_column_keys(self) -> WorkbookConfig:
        for workbook_key in self.column_keys.values():
            if not _ASCII_KEY.fullmatch(workbook_key):
                raise ValueError(f"workbook column key must be ASCII: {workbook_key!r}")
        if len(set(self.column_keys.values())) != len(self.column_keys):
            raise ValueError("workbook column keys must be unique")
        return self


class DocumentConfig(_StrictModel):
    """Document stage parameters for the report@1 template request."""

    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    currency: str = Field(min_length=1, max_length=12)
    customer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    summary_lines: list[str] = Field(default_factory=list, max_length=40)


class PipelineReportConfig(_StrictModel):
    """Typed view of the ``pipeline-report-config.v1`` job configuration."""

    schema_version: Literal["pipeline-report-config.v1"]
    pipeline_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    dataset: DatasetConfig
    chart: ChartConfig
    workbook: WorkbookConfig
    document: DocumentConfig
    stage_timeout_seconds: int = Field(default=300, ge=MIN_STAGE_TIMEOUT_SECONDS, le=MAX_STAGE_TIMEOUT_SECONDS)

    @model_validator(mode="after")
    def _check_chart_columns(self) -> PipelineReportConfig:
        keys = {column.key for column in self.dataset.columns}
        if self.chart.x_column not in keys:
            raise ValueError(f"chart.x_column is not a dataset column: {self.chart.x_column!r}")
        if self.chart.y_column is not None and self.chart.y_column not in keys:
            raise ValueError(f"chart.y_column is not a dataset column: {self.chart.y_column!r}")
        if self.chart.aggregation in {"sum", "avg"}:
            if self.chart.y_column is None:
                raise ValueError("chart.y_column is required for sum and avg aggregations")
            types = {column.key: column.data_type for column in self.dataset.columns}
            if types[self.chart.y_column] not in {"integer", "decimal"}:
                raise ValueError("chart.y_column must be integer or decimal for sum and avg aggregations")
        return self


def parse_config(payload: Any) -> PipelineReportConfig:
    """Validate a raw config payload structurally and semantically."""

    issues = validate_config_payload(payload)
    if issues:
        raise ContractError(CONTRACT_CONFIG, issues)
    try:
        config = PipelineReportConfig.model_validate(payload)
    except Exception as exc:
        raise ContractError(CONTRACT_CONFIG, [str(exc)]) from exc
    _check_dataset_headers(config)
    return config


def _check_dataset_headers(config: PipelineReportConfig) -> None:
    """Fail closed when a declared column is missing from the CSV header row."""

    reader = csv.reader(io.StringIO(config.dataset.csv_text, newline=""), delimiter=config.dataset.delimiter)
    try:
        headers = next(reader)
    except StopIteration:
        raise ContractError(CONTRACT_CONFIG, ["dataset.csv_text has no header row"]) from None
    except csv.Error as exc:
        raise ContractError(CONTRACT_CONFIG, [f"dataset.csv_text is not parseable CSV: {exc}"]) from None
    missing = [
        column.key
        for column in config.dataset.columns
        if (column.source_name or column.key) not in headers
    ]
    if missing:
        raise ContractError(CONTRACT_CONFIG, [f"dataset columns missing from CSV header: {', '.join(missing)}"])


def parse_config_file(path: str | Path) -> PipelineReportConfig:
    """Read a config JSON file and parse it as ``pipeline-report-config.v1``."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(CONTRACT_CONFIG, [f"cannot read config: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise ContractError(CONTRACT_CONFIG, [f"config is not valid JSON: {exc}"]) from exc
    return parse_config(payload)


class StageArtifact(BaseModel):
    """Artifact reference consumed from upstream stage job manifests."""

    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    byte_size: int = Field(ge=0)


class StageJobManifest(BaseModel):
    """Required subset of an upstream job manifest (data/spreadsheet/document).

    The documents service publishes an unversioned manifest; the data and
    spreadsheet services publish ``data-job.v1`` / ``spreadsheet-job.v1``.
    Only the fields this orchestrator consumes are required; unknown fields
    are ignored.
    """

    model_config = ConfigDict(extra="ignore")

    status: str
    job_id: str | None = None
    error_code: str | None = None
    artifacts: list[StageArtifact] = Field(default_factory=list)
    artifact: StageArtifact | None = None

    def all_artifacts(self) -> list[StageArtifact]:
        return [*self.artifacts, *([self.artifact] if self.artifact is not None else [])]


class ManifestArtifact(_StrictModel):
    """Artifact identity recorded in the published pipeline manifest."""

    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=120)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_path(self) -> ManifestArtifact:
        check_relative_path(self.path)
        return self


STAGE_NAMES: tuple[str, ...] = ("data_pipeline", "data_viz", "spreadsheet", "documents")
StageName = Literal["data_pipeline", "data_viz", "spreadsheet", "documents"]


class StageRecord(_StrictModel):
    """One accepted pipeline stage in the published manifest."""

    stage: StageName
    status: Literal["accepted"]
    tool: str = Field(min_length=1, max_length=200)
    command: list[str] = Field(min_length=1, max_length=64)
    input_contract: str = Field(min_length=1, max_length=80)
    output_contract: str = Field(min_length=1, max_length=80)
    request_path: str = Field(min_length=1, max_length=500)
    manifest_path: str = Field(min_length=1, max_length=500)
    job_id: str = Field(min_length=1, max_length=128)
    artifacts: list[ManifestArtifact] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _check_paths(self) -> StageRecord:
        check_relative_path(self.request_path)
        check_relative_path(self.manifest_path)
        for arg in self.command:
            if "\\" in arg or arg.startswith("/") or _DRIVE_PREFIX.match(arg):
                raise ValueError(f"command must not contain absolute local paths: {arg!r}")
        return self


class PipelineReportManifest(_StrictModel):
    """Typed view of the published ``pipeline-report.v1`` manifest."""

    schema_version: Literal["pipeline-report.v1"]
    pipeline_id: str = Field(min_length=1, max_length=64)
    config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_calls: Literal[0] = 0
    stages: list[StageRecord] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _check_stage_order(self) -> PipelineReportManifest:
        stages = tuple(record.stage for record in self.stages)
        if stages != STAGE_NAMES:
            raise ValueError(f"stages must appear in fixed order: {STAGE_NAMES}")
        return self


def parse_manifest(payload: Any) -> PipelineReportManifest:
    """Validate a raw manifest payload structurally and semantically."""

    issues = validate_manifest_payload(payload)
    if issues:
        raise ContractError(CONTRACT_MANIFEST, issues)
    try:
        return PipelineReportManifest.model_validate(payload)
    except Exception as exc:
        raise ContractError(CONTRACT_MANIFEST, [str(exc)]) from exc


def workbook_key_for(data_key: str, index: int, mapping: dict[str, str]) -> str:
    """Deterministically map a dataset column key to an ASCII workbook key."""

    if data_key in mapping:
        return mapping[data_key]
    if _ASCII_KEY.fullmatch(data_key):
        return data_key
    return f"col_{index:02d}"


__all__ = [
    "CONTRACT_CONFIG",
    "CONTRACT_DATASET_REQUEST",
    "CONTRACT_DATA_JOB",
    "CONTRACT_DOCUMENT_MANIFEST",
    "CONTRACT_MANIFEST",
    "CONTRACT_REPORT_REQUEST",
    "CONTRACT_SPREADSHEET_JOB",
    "ChartConfig",
    "ColumnSpecConfig",
    "ContractError",
    "DatasetConfig",
    "DocumentConfig",
    "ManifestArtifact",
    "PipelineReportConfig",
    "PipelineReportManifest",
    "StageArtifact",
    "StageJobManifest",
    "StageRecord",
    "WorkbookConfig",
    "check_relative_path",
    "load_schema",
    "parse_config",
    "parse_config_file",
    "parse_manifest",
    "schema_issues",
    "validate_config_payload",
    "validate_manifest_payload",
    "workbook_key_for",
]
