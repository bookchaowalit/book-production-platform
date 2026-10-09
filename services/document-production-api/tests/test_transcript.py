import json
from pathlib import Path

import pytest
from document_production.cli import main
from document_production.jobs import run_transcript_job
from document_production.qa import qa_document
from document_production.renderers import RenderError, render_document
from document_production.transcript import (
    TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_PATH,
    TRANSCRIPT_RECIPE_ID,
    build_transcript_ir,
    format_document_timestamp,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

GOLDEN_REQUEST: dict = {
    "schema_version": "transcript-document-request.v1",
    "document_id": "demo-001",
    "title": "บทถอดเสียง การทดสอบ",
    "locale": "th-TH",
    "issue_date": "2026-09-26",
    "source_name": "demo.mp4",
    "language": "th",
    "source_tool": "faster-whisper",
    "segments": [
        {"start_seconds": 0.0, "end_seconds": 2.5, "text": "สวัสดีครับ นี่คือการทดสอบระบบถอดเสียง"},
        {"start_seconds": 2.5, "end_seconds": 6.0, "text": "Hello from the synthetic transcript fixture", "speaker": "Host"},
        {"start_seconds": 6.0, "end_seconds": 9.5, "text": "ปิดการทดสอบ"},
    ],
}


def test_recipe_formats_timestamped_verbatim_paragraphs():
    ir = build_transcript_ir(GOLDEN_REQUEST)
    assert ir.template_ref == "transcript@1"
    assert ir.sections[0].heading == "บทถอดเสียง"
    assert ir.sections[0].paragraphs == [
        "[00:00:00.000 - 00:00:02.500] สวัสดีครับ นี่คือการทดสอบระบบถอดเสียง",
        "[00:00:02.500 - 00:00:06.000] Host: Hello from the synthetic transcript fixture",
        "[00:00:06.000 - 00:00:09.500] ปิดการทดสอบ",
    ]
    assert ir.derived_totals == {}
    assert ir.assumptions["recipe"] == TRANSCRIPT_RECIPE_ID
    assert ir.assumptions["model_calls"] == "0"
    assert ir.assumptions["segment_count"] == "3"


def test_recipe_uses_english_heading_for_non_thai_locales():
    ir = build_transcript_ir({**GOLDEN_REQUEST, "locale": "en-US"})
    assert ir.sections[0].heading == "Transcript"


def test_document_timestamp_pin_points_milliseconds():
    assert format_document_timestamp(3661.5) == "01:01:01.500"
    assert format_document_timestamp(0.0) == "00:00:00.000"
    with pytest.raises(ValueError):
        format_document_timestamp(-0.001)


def test_request_rejects_out_of_order_segments():
    payload = {**GOLDEN_REQUEST, "segments": [{"start_seconds": 2.5, "end_seconds": 2.5, "text": "a"}]}
    with pytest.raises(ValidationError):
        build_transcript_ir(payload)


def test_request_rejects_unknown_schema_version():
    with pytest.raises(ValidationError):
        build_transcript_ir({**GOLDEN_REQUEST, "schema_version": "transcript-document-request.v2"})


def test_request_rejects_extra_properties():
    with pytest.raises(ValidationError):
        build_transcript_ir({**GOLDEN_REQUEST, "unexpected": True})


def test_published_schema_validates_the_golden_request():
    schema = json.loads(TRANSCRIPT_DOCUMENT_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(GOLDEN_REQUEST)
    assert schema["$id"] == "urn:solo-empire:document-production:transcript-document-request.v1"


def test_transcript_renders_and_passes_inherited_qa_or_fails_loudly(tmp_path):
    ir = build_transcript_ir(GOLDEN_REQUEST)
    try:
        artifacts = render_document(ir, tmp_path)
    except RenderError as exc:
        # Without a Thai-capable font the render must fail loudly instead of
        # substituting silent '?' placeholders for Thai text.
        assert "font" in str(exc)
        return
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "passed", result.issues
    assert all(issue["code"] != "document_text_missing" for issue in result.issues)


def test_transcript_job_is_accepted_idempotently_with_zero_model_calls(tmp_path):
    first = run_transcript_job(GOLDEN_REQUEST, tmp_path, "transcript-001")
    assert first.status == "accepted"
    assert first.model_calls == 0
    manifest = json.loads(Path(str(first.manifest_path)).read_text(encoding="utf-8"))
    assert manifest["recipe"] == "transcript-v1"
    assert manifest["qa"]["status"] == "passed"
    assert {artifact["filename"] for artifact in manifest["artifacts"]} == {"document.docx", "document.pdf"}

    second = run_transcript_job(GOLDEN_REQUEST, tmp_path, "transcript-001")
    assert second.status == "accepted"
    assert second.job_id == first.job_id
    assert second.cost_event["replayed"] is True


def test_transcript_job_reports_invalid_segments_as_failed(tmp_path):
    payload = {**GOLDEN_REQUEST, "segments": [{"start_seconds": 5.0, "end_seconds": 1.0, "text": "ย้อนเวลา"}]}
    result = run_transcript_job(payload, tmp_path, "transcript-invalid")
    assert result.status == "failed"
    assert result.manifest_path is None
    assert result.error_code == "ValidationError"


def test_transcript_job_reports_wrong_schema_version_as_failed(tmp_path):
    result = run_transcript_job({**GOLDEN_REQUEST, "schema_version": "transcript-document-request.v2"}, tmp_path, "transcript-schema")
    assert result.status == "failed"
    assert result.manifest_path is None


def test_transcript_job_rejects_conflicting_idempotency_key(tmp_path):
    first = run_transcript_job(GOLDEN_REQUEST, tmp_path, "transcript-conflict")
    assert first.status == "accepted"
    changed = {**GOLDEN_REQUEST, "title": "ชื่ออื่น"}
    with pytest.raises(Exception, match="idempotency"):
        run_transcript_job(changed, tmp_path, "transcript-conflict")


def test_cli_transcript_renders_the_emitted_request(tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(GOLDEN_REQUEST, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "out"

    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = main(
            [
                "transcript",
                "--input", str(request_path),
                "--output", str(output_dir),
                "--idempotency-key", "cli-transcript-001",
            ]
        )
    assert exit_code == 0
    assert "accepted" in buffer.getvalue()
    manifests = list(output_dir.rglob("manifest.json"))
    assert len(manifests) == 1


def _docx_paragraph_text(path: Path) -> str:
    from xml.etree import ElementTree
    from zipfile import ZipFile

    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    return " ".join(element.text or "" for element in root.iter() if element.tag.endswith("}t"))


def test_transcript_documents_omit_the_invoice_totals_block(tmp_path):
    ir = build_transcript_ir(GOLDEN_REQUEST)
    render_document(ir, tmp_path)
    assert "Totals" not in _docx_paragraph_text(tmp_path / "document.docx")


def test_invoice_documents_still_publish_the_totals_block(tmp_path):
    from document_production.recipe import build_document_ir

    invoice_request = {
        "schema_version": "document-request.v1",
        "template_id": "invoice",
        "template_version": "1",
        "document_id": "doc-001",
        "currency": "THB",
        "title": "Invoice",
        "customer_name": "Test customer",
        "issue_date": "2026-09-26",
        "line_items": [{"description": "Service", "quantity": "2", "unit_price": "10.00", "tax_rate": "7"}],
    }
    ir = build_document_ir(invoice_request)
    render_document(ir, tmp_path)
    text = _docx_paragraph_text(tmp_path / "document.docx")
    assert "Totals" in text
    assert "grand_total: 21.40 THB" in text
