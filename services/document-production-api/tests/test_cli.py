import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace

from document_ir import DocumentRequest
from document_production.cli import main
from document_production.job_store import JobStore
from document_production.jobs import _hash_json


def run_cli(*args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        return_code = main(list(args))
    return SimpleNamespace(returncode=return_code, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def test_cli_rejects_unknown_template(tmp_path):
    result = run_cli("render", "--input", "examples/unknown.json", "--output", str(tmp_path), "--idempotency-key", "cli-001")
    assert result.returncode != 0
    assert "input" in result.stderr.lower()


def test_cli_rejects_schema_invalid_request_without_traceback(tmp_path):
    request_path = tmp_path / "invalid.json"
    request_path.write_text('{"template_id": "invoice"}', encoding="utf-8")
    result = run_cli("render", "--input", str(request_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-invalid")
    assert result.returncode == 2
    assert "validation" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_cli_reports_pending_job_with_distinct_exit_code(tmp_path):
    request = {
        "template_id": "invoice",
        "template_version": "1",
        "document_id": "cli-running",
        "currency": "THB",
        "title": "Invoice",
        "customer_name": "CLI customer",
        "issue_date": "2026-09-22",
        "line_items": [{"description": "Service", "quantity": "1", "unit_price": "100", "tax_rate": "7"}],
    }
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "out"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    parsed = DocumentRequest.model_validate(request)
    JobStore(output_path).claim(
        key_hash=hashlib.sha256(b"cli-running").hexdigest(),
        request_hash=_hash_json(parsed.model_dump(mode="json")),
        job_id="running-job",
        recipe_version="invoice-v1",
        schema_version=parsed.schema_version,
        engine_version="document-engine.v1",
    )
    result = run_cli("render", "--input", str(request_path), "--output", str(output_path), "--idempotency-key", "cli-running")
    assert result.returncode == 3
    assert "pending" in result.stderr.lower()


def test_cli_brief_reports_needs_input_with_distinct_exit_code(tmp_path):
    request_path = tmp_path / "brief.json"
    request_path.write_text(json.dumps({"document_id": "cli-brief", "brief": "title: Missing"}), encoding="utf-8")

    result = run_cli("brief", "--input", str(request_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-brief")

    assert result.returncode == 4
    assert "needs_input" in result.stderr.lower()
    assert "customer_name" in result.stderr


def test_cli_scan_writes_proposal_and_reports_success(tmp_path):
    from reportlab.pdfgen import canvas

    source = tmp_path / "invoice.pdf"
    pdf = canvas.Canvas(str(source))
    for y, line in zip(
        (740, 716, 692, 668, 644),
        (
            "title: Synthetic invoice",
            "customer: Example customer",
            "date: 2026-09-22",
            "currency: USD",
            "item: Service | 1 | 10.00 | 0",
        ),
    ):
        pdf.drawString(40, y, line)
    pdf.save()
    input_path = tmp_path / "scan.json"
    input_path.write_text(
        json.dumps({"document_id": "cli-scan", "source_pdf": "invoice.pdf", "locale": "en-US"}),
        encoding="utf-8",
    )
    output_path = tmp_path / "proposal.json"

    result = run_cli("scan", "--input", str(input_path), "--input-root", str(tmp_path), "--output", str(output_path))

    proposal = json.loads(output_path.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert proposal["status"] == "complete"
    assert proposal["model_calls"] == 0


def test_cli_scan_saves_needs_input_proposal_with_distinct_exit_code(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    monkeypatch.setattr("document_production.cli.TesseractOCRAdapter.from_system", lambda: None)
    source = tmp_path / "sparse.pdf"
    pdf = canvas.Canvas(str(source))
    pdf.drawString(40, 740, "title: Sparse")
    pdf.save()
    input_path = tmp_path / "scan.json"
    input_path.write_text(
        json.dumps({"document_id": "cli-scan-missing", "source_pdf": "sparse.pdf", "locale": "en-US"}),
        encoding="utf-8",
    )
    output_path = tmp_path / "proposal.json"

    result = run_cli("scan", "--input", str(input_path), "--input-root", str(tmp_path), "--output", str(output_path))

    proposal = json.loads(output_path.read_text(encoding="utf-8"))
    assert result.returncode == 4
    assert proposal["status"] == "needs_input"
    assert proposal["qa"]["accepted"] is False


def test_cli_smoke_runs_without_provider_config(tmp_path):
    result = run_cli("smoke", "--output", str(tmp_path / "out"))

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "accepted"
    assert payload["model_calls"] == 0
    assert payload["qa_status"] == "passed"
    assert payload["artifacts"] == ["document.docx", "document.pdf"]
    manifests = list((tmp_path / "out").rglob("manifest.json"))
    assert len(manifests) == 1
    for filename in payload["artifacts"]:
        assert (manifests[0].parent / filename).is_file()


def test_cli_smoke_replays_idempotently_without_rerendering(tmp_path):
    first = run_cli("smoke", "--output", str(tmp_path / "out"))
    second = run_cli("smoke", "--output", str(tmp_path / "out"))

    assert first.returncode == 0 and second.returncode == 0
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is True
    assert second_payload["job_id"] == first_payload["job_id"]


def test_cli_reports_idempotency_conflict_with_distinct_exit_code(tmp_path):
    request = {
        "template_id": "invoice",
        "template_version": "1",
        "document_id": "cli-conflict",
        "currency": "THB",
        "title": "Invoice",
        "customer_name": "CLI customer",
        "issue_date": "2026-09-22",
        "line_items": [{"description": "Service", "quantity": "1", "unit_price": "100", "tax_rate": "7"}],
    }
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "out"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    first = run_cli("render", "--input", str(request_path), "--output", str(output_path), "--idempotency-key", "cli-conflict")
    assert first.returncode == 0

    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps({**request, "title": "Changed"}), encoding="utf-8")
    conflict = run_cli("render", "--input", str(changed_path), "--output", str(output_path), "--idempotency-key", "cli-conflict")

    assert conflict.returncode == 5
    assert "idempotency" in conflict.stderr.lower()
    assert "--idempotency-key" in conflict.stderr
