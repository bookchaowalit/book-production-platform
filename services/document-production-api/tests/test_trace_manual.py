"""Job history, trace, manual Word guide, and page-number footer for Documents."""

from __future__ import annotations

import json
from pathlib import Path

import docx
from production_trace import read_history, verify_trace
from pypdf import PdfReader
from test_recipe import sample_request

from document_production.jobs import run_document_job

CANARY = "CANARY-trace-customer"


def _job(tmp_path: Path, **overrides):
    request = sample_request()
    request.update(overrides)
    return run_document_job(request, tmp_path, "trace-key")


def test_accepted_job_writes_trace_history_ir_and_manual(tmp_path):
    result = _job(tmp_path, customer_name=CANARY)
    job_dir = tmp_path / "jobs" / result.job_id

    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["schema_version"] == "production-trace.v1"
    assert [step["stage"] for step in trace["steps"]] == ["validate", "recipe", "render", "qa", "publish"]
    assert {artifact["file"] for artifact in trace["artifacts"]} == {"document.docx", "document.pdf"}
    assert trace["ir"]["file"] == "ir.json" and (job_dir / "ir.json").is_file()
    assert CANARY not in (job_dir / "trace.json").read_text(encoding="utf-8")
    assert CANARY not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")

    history = read_history(tmp_path)
    assert history[-1]["job_id"] == result.job_id and history[-1]["status"] == "accepted"
    assert verify_trace(tmp_path, result.job_id)["ok"] is True


def test_manual_guide_mirrors_the_ir(tmp_path):
    result = _job(tmp_path)
    job_dir = tmp_path / "jobs" / result.job_id
    ir = json.loads((job_dir / "ir.json").read_text(encoding="utf-8"))
    manual = (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")

    assert "วิธีทำเองด้วย Microsoft Word" in manual
    assert f"`{ir['title']}`" in manual
    for section in ir["sections"]:
        assert f"`{section['heading']}`" in manual
        for table in section["tables"]:
            assert " | ".join(table["headers"]) in manual
            for row in table["rows"]:
                assert " | ".join(row["cells"]) in manual
    for name, value in ir["derived_totals"].items():
        assert f"{name}: {value} {ir['currency']}" in manual
    assert "Insert ▸ Page Number" in manual


def test_tampered_artifact_fails_verification(tmp_path):
    result = _job(tmp_path)
    (tmp_path / "jobs" / result.job_id / "document.pdf").write_bytes(b"tampered")
    assert verify_trace(tmp_path, result.job_id)["ok"] is False


def test_failed_job_is_still_in_history(tmp_path):
    result = run_document_job({**sample_request(), "line_items": []}, tmp_path, "trace-fail")
    assert result.status == "failed"
    entries = [entry for entry in read_history(tmp_path) if entry["job_id"] == result.job_id]
    assert entries[-1]["status"] == "failed"
    trace = json.loads((tmp_path / entries[-1]["trace"]).read_text(encoding="utf-8"))
    assert trace["steps"][-1]["operation"] == "failed"
    assert not (tmp_path / "jobs" / result.job_id / "manifest.json").exists()


def test_footer_carries_document_id_and_page_number(tmp_path):
    result = _job(tmp_path)
    job_dir = tmp_path / "jobs" / result.job_id
    footer = docx.Document(str(job_dir / "document.docx")).sections[0].footer.paragraphs[0]
    assert footer.text.startswith(sample_request()["document_id"])
    assert "PAGE" in footer._p.xml
    assert f"{sample_request()['document_id']} · " in PdfReader(str(job_dir / "document.pdf")).pages[0].extract_text()

