"""Tests for the deterministic tabular report recipe (Documents V1A/T6.3)."""

import json

import pytest
from document_production.cli import main
from document_production.jobs import run_report_job
from document_production.qa import qa_document
from document_production.renderers import render_document
from document_production.report import (
    REPORT_DOCUMENT_REQUEST_SCHEMA_PATH,
    REPORT_RECIPE_ID,
    build_report_ir,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

GOLDEN_REQUEST: dict = {
    "schema_version": "report-document-request.v1",
    "document_id": "report-2026-09",
    "title": "รายงานรายเดือน กันยายน 2026",
    "locale": "th-TH",
    "currency": "THB",
    "customer_name": "ฝ่ายขาย",
    "issue_date": "2026-09-30",
    "summary_lines": ["ช่วงข้อมูล: กันยายน 2026"],
    "table": {
        "headers": ["หมวด", "ยอดขาย"],
        "rows": [["อาหาร", "2000.50"], ["เครื่องดื่ม", "750.75"], ["ของใช้", "220"]],
    },
    "totals": {"subtotal": "2971.25", "tax_total": "0", "grand_total": "2971.25"},
    "charts": [
        {
            "name": "chart.svg",
            "media_type": "image/svg+xml",
            "content_hash": "a" * 64,
            "byte_size": 2276,
        }
    ],
    "workbook": {
        "name": "workbook.xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content_hash": "b" * 64,
        "byte_size": 6656,
    },
}


def test_recipe_builds_tabular_report_ir():
    ir = build_report_ir(GOLDEN_REQUEST)
    assert ir.template_ref == "report@1"
    assert [section.heading for section in ir.sections] == ["รายงาน", "ข้อมูลรายงาน", "แผนภูมิ"]
    assert ir.sections[1].tables[0].headers == ["หมวด", "ยอดขาย"]
    assert ir.derived_totals == {"subtotal": "2971.25", "tax_total": "0", "grand_total": "2971.25"}
    assert ir.assumptions["recipe"] == REPORT_RECIPE_ID
    assert ir.assumptions["model_calls"] == "0"
    assert ir.assumptions["row_count"] == "3"
    assert ir.assumptions["chart_count"] == "1"


def test_recipe_uses_english_headings_for_non_thai_locales():
    ir = build_report_ir({**GOLDEN_REQUEST, "locale": "en-US"})
    assert [section.heading for section in ir.sections] == ["Report", "Report data", "Charts"]


def test_recipe_rejects_subtotal_not_matching_table_sum():
    payload = {**GOLDEN_REQUEST, "totals": {"subtotal": "999", "tax_total": "0", "grand_total": "999"}}
    with pytest.raises(Exception) as excinfo:
        build_report_ir(payload)
    assert "subtotal_mismatch" in str(excinfo.value)


def test_recipe_rejects_grand_total_not_matching_sum():
    payload = {**GOLDEN_REQUEST, "totals": {"subtotal": "2971.25", "tax_total": "10", "grand_total": "2971.25"}}
    with pytest.raises(Exception) as excinfo:
        build_report_ir(payload)
    assert "grand_total_mismatch" in str(excinfo.value)


def test_recipe_rejects_non_numeric_last_column():
    payload = json.loads(json.dumps(GOLDEN_REQUEST))
    payload["table"]["rows"][0][1] = "หลายบาท"
    with pytest.raises(ValidationError):
        build_report_ir(payload)


def test_request_rejects_unknown_schema_version():
    with pytest.raises(ValidationError):
        build_report_ir({**GOLDEN_REQUEST, "schema_version": "report-document-request.v2"})


def test_request_rejects_extra_properties():
    with pytest.raises(ValidationError):
        build_report_ir({**GOLDEN_REQUEST, "unexpected": True})


def test_request_rejects_row_with_wrong_cell_count():
    payload = json.loads(json.dumps(GOLDEN_REQUEST))
    payload["table"]["rows"].append(["only-one-cell"])
    with pytest.raises(ValidationError):
        build_report_ir(payload)


def test_published_schema_validates_the_golden_request():
    schema = json.loads(REPORT_DOCUMENT_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(GOLDEN_REQUEST)


def test_render_and_qa_pass_with_thai_text(tmp_path):
    ir = build_report_ir(GOLDEN_REQUEST)
    artifacts = render_document(ir, tmp_path)
    qa = qa_document(ir, artifacts, tmp_path)
    assert qa.status == "passed", qa.issues


def test_run_report_job_accepts_and_publishes_manifest(tmp_path):
    result = run_report_job(dict(GOLDEN_REQUEST), tmp_path, "report-golden-1")
    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["recipe"] == REPORT_RECIPE_ID
    assert [artifact["filename"] for artifact in manifest["artifacts"]] == ["document.docx", "document.pdf"]


def test_cli_report_command_renders(tmp_path):
    request_path = tmp_path / "report-request.json"
    request_path.write_text(json.dumps(GOLDEN_REQUEST, ensure_ascii=False), encoding="utf-8")
    exit_code = main(
        [
            "report",
            "--input", str(request_path),
            "--output", str(tmp_path / "out"),
            "--idempotency-key", "report-cli-1",
        ]
    )
    assert exit_code == 0
    assert list((tmp_path / "out").glob("jobs/*/document.pdf"))


def test_cli_report_command_rejects_invalid_request(tmp_path):
    request_path = tmp_path / "report-request.json"
    payload = {**GOLDEN_REQUEST, "totals": {"subtotal": "1", "tax_total": "0", "grand_total": "2"}}
    request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    exit_code = main(
        [
            "report",
            "--input", str(request_path),
            "--output", str(tmp_path / "out"),
            "--idempotency-key", "report-cli-2",
        ]
    )
    assert exit_code == 2
