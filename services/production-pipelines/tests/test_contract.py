"""Contract tests for the versioned pipeline contracts and validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synthetic_fixtures import synthetic_config_payload

from production_pipelines.contracts import (
    CONTRACT_MANIFEST,
    ContractError,
    parse_config,
    parse_manifest,
    workbook_key_for,
)

REPO_ROOT = Path(__file__).parents[3]
DOCUMENTS_SCHEMA = REPO_ROOT / "services" / "document-production-api" / "schemas" / "report-document-request.v1.schema.json"
LOCAL_REPORT_SCHEMA = Path(__file__).parents[1] / "schemas" / "report-document-request.v1.schema.json"


def test_valid_config_parses():
    config = parse_config(synthetic_config_payload())
    assert config.pipeline_id == "monthly-report-2026-09"
    assert config.chart.aggregation == "sum"
    assert config.dataset.columns[1].data_type == "decimal"


def test_config_rejects_unknown_schema_version():
    payload = synthetic_config_payload()
    payload["schema_version"] = "pipeline-report-config.v2"
    with pytest.raises(ContractError) as excinfo:
        parse_config(payload)
    assert "schema_version" in str(excinfo.value)


def test_config_rejects_chart_column_missing_from_dataset():
    payload = synthetic_config_payload()
    payload["chart"]["x_column"] = "unknown"
    with pytest.raises(ContractError):
        parse_config(payload)


def test_config_rejects_sum_without_y_column():
    payload = synthetic_config_payload()
    payload["chart"]["y_column"] = None
    payload["chart"]["aggregation"] = "sum"
    with pytest.raises(ContractError):
        parse_config(payload)


def test_config_rejects_non_numeric_y_column_for_sum():
    payload = synthetic_config_payload()
    payload["chart"]["y_column"] = "category"
    with pytest.raises(ContractError):
        parse_config(payload)


def test_config_rejects_column_missing_from_csv_header():
    payload = synthetic_config_payload()
    payload["dataset"]["columns"].append({"key": "extra", "label": "x", "data_type": "text"})
    with pytest.raises(ContractError) as excinfo:
        parse_config(payload)
    assert "extra" in str(excinfo.value)


def test_manifest_rejects_absolute_command_paths():
    manifest = {
        "schema_version": CONTRACT_MANIFEST,
        "pipeline_id": "p",
        "config_hash": "a" * 64,
        "model_calls": 0,
        "stages": [_stage_record("C:\\Windows\\python.exe")],
    }
    with pytest.raises(ContractError):
        parse_manifest(manifest)


def test_manifest_rejects_wrong_stage_order():
    records = [_stage_record("python") for _ in range(4)]
    records[0], records[1] = records[1], records[0]
    manifest = {
        "schema_version": CONTRACT_MANIFEST,
        "pipeline_id": "p",
        "config_hash": "a" * 64,
        "model_calls": 0,
        "stages": records,
    }
    with pytest.raises(ContractError):
        parse_manifest(manifest)


def test_manifest_rejects_absolute_artifact_path():
    record = _stage_record("python")
    record["artifacts"][0]["path"] = "C:/abs/path/cleaned.csv"
    manifest = {
        "schema_version": CONTRACT_MANIFEST,
        "pipeline_id": "p",
        "config_hash": "a" * 64,
        "model_calls": 0,
        "stages": [record],
    }
    with pytest.raises(ContractError):
        parse_manifest(manifest)


def _stage_record(interpreter: str) -> dict:
    return {
        "stage": "data_pipeline",
        "status": "accepted",
        "tool": "t",
        "command": [interpreter, "-m", "data_production.cli", "build"],
        "input_contract": "dataset-request.v1",
        "output_contract": "data-job.v1",
        "request_path": "stages/01-data-pipeline/request.json",
        "manifest_path": "stages/01-data-pipeline/job-x/manifest.json",
        "job_id": "job-1",
        "artifacts": [
            {
                "name": "cleaned.csv",
                "path": "stages/01-data-pipeline/job-x/cleaned.csv",
                "media_type": "text/csv",
                "content_hash": "b" * 64,
                "byte_size": 10,
            }
        ],
    }


def test_workbook_key_mapping_is_deterministic():
    assert workbook_key_for("category", 1, {}) == "category"
    assert workbook_key_for("หมวด", 3, {}) == "col_03"
    assert workbook_key_for("หมวด", 3, {"หมวด": "category_th"}) == "category_th"


def test_workbook_key_mapping_rejects_duplicates_in_model():
    payload = synthetic_config_payload()
    payload["workbook"]["column_keys"] = {"category": "same", "amount": "same"}
    with pytest.raises(ContractError):
        parse_config(payload)


def test_local_report_schema_matches_documents_service_copy():
    """The consumer-side schema copy stays aligned with the documents service."""

    assert json.loads(LOCAL_REPORT_SCHEMA.read_text(encoding="utf-8")) == json.loads(
        DOCUMENTS_SCHEMA.read_text(encoding="utf-8")
    )


def test_owned_schema_files_parse():
    from production_pipelines.contracts import SCHEMA_FILES, load_schema

    for contract in SCHEMA_FILES:
        schema = load_schema(contract)
        assert schema["$schema"].endswith("json-schema.org/draft/2020-12/schema")
