import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from document_ir import DocumentRequest
from document_production.extraction import BriefRequest, ExtractionResult
from document_production.job_store import JobStore
from document_production.jobs import (
    IdempotencyConflict,
    _hash_json,
    run_document_brief_job,
    run_document_job,
)
from test_recipe import sample_request


def test_job_reports_zero_model_calls_and_writes_manifest(tmp_path):
    result = run_document_job(sample_request(), tmp_path, "idem-001")
    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.manifest_path is not None
    assert json.loads(result.manifest_path.read_text(encoding="utf-8"))["qa"]["status"] == "passed"


def test_same_idempotency_key_returns_same_result(tmp_path):
    first = run_document_job(sample_request(), tmp_path, "idem-001")
    second = run_document_job(sample_request(), tmp_path, "idem-001")
    assert second.job_id == first.job_id
    assert second.manifest_path == first.manifest_path


def test_replay_event_does_not_recount_existing_artifact_bytes(tmp_path):
    first = run_document_job(sample_request(), tmp_path, "idem-replay")
    second = run_document_job(sample_request(), tmp_path, "idem-replay")
    assert first.cost_event["replayed"] is False
    assert second.cost_event["replayed"] is True
    assert second.cost_event["attempt_id"] != first.cost_event["attempt_id"]
    assert second.cost_event["bytes_written"] == 0
    assert second.cost_event["wall_seconds"] >= 0


def test_running_claim_returns_pending_without_rendering(tmp_path):
    key = "idem-running"
    store = JobStore(tmp_path)
    request_hash = _hash_json(DocumentRequest.model_validate(sample_request()).model_dump(mode="json"))
    store.claim(
        key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        request_hash=request_hash,
        job_id="job-running",
        recipe_version="invoice-v1",
        schema_version="document-request.v1",
        engine_version="document-engine.v1",
    )
    result = run_document_job(sample_request(), tmp_path, key)
    assert result.status == "pending"
    assert result.error_code == "job_in_progress"
    assert result.manifest_path is None
    assert not list(tmp_path.rglob("manifest.json"))


def test_concurrent_duplicate_requests_publish_one_artifact_set(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_document_job(sample_request(), tmp_path, "idem-concurrent"), range(2)))

    accepted = [result for result in results if result.status == "accepted"]
    pending = [result for result in results if result.status == "pending"]
    assert len(accepted) == 1
    assert len(pending) == 1
    manifests = list(tmp_path.rglob("manifest.json"))
    assert len(manifests) == 1
    assert manifests[0].parent == tmp_path / "jobs" / accepted[0].job_id
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["status"] == "accepted"


def test_crashed_attempt_does_not_publish_unaccepted_artifacts(tmp_path):
    key = "idem-crash"
    parsed = DocumentRequest.model_validate(sample_request())
    JobStore(tmp_path).claim(
        key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        request_hash=_hash_json(parsed.model_dump(mode="json")),
        job_id="crashed-job",
        recipe_version="invoice-v1",
        schema_version=parsed.schema_version,
        engine_version="document-engine.v1",
        lease_seconds=0,
    )
    crashed_dir = tmp_path / "jobs" / "crashed-job"
    crashed_dir.mkdir(parents=True)
    (crashed_dir / "document.pdf").write_bytes(b"partial render from crashed attempt")

    result = run_document_job(sample_request(), tmp_path, key)

    assert result.status == "accepted"
    assert result.job_id != "crashed-job"
    assert not list(crashed_dir.rglob("manifest.json"))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["qa"]["status"] == "passed"
    assert (result.manifest_path.parent / "document.pdf").read_bytes() != b"partial render from crashed attempt"


def test_job_uses_sqlite_ledger_and_rebuilds_missing_artifact(tmp_path):
    first = run_document_job(sample_request(), tmp_path, "idem-ledger")
    assert (tmp_path / "jobs.sqlite3").exists()
    assert first.manifest_path is not None
    artifact = first.manifest_path.parent / "document.pdf"
    artifact.unlink()
    rebuilt = run_document_job(sample_request(), tmp_path, "idem-ledger")
    assert rebuilt.status == "accepted"
    assert rebuilt.job_id == first.job_id
    assert artifact.exists()


def test_changed_input_with_same_key_does_not_overwrite(tmp_path):
    first = run_document_job(sample_request(), tmp_path, "idem-001")
    with pytest.raises(IdempotencyConflict):
        run_document_job(sample_request(title="changed"), tmp_path, "idem-001")
    assert first.manifest_path is not None and first.manifest_path.exists()


def test_failed_renderer_has_no_accepted_manifest(tmp_path, monkeypatch):
    def fail_renderer(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr("document_production.jobs.render_document", fail_renderer)
    result = run_document_job(sample_request(), tmp_path, "idem-002")
    assert result.status == "failed"
    assert result.manifest_path is None
    assert not list(tmp_path.rglob("manifest.json"))


def test_brief_job_uses_parser_then_v1a_renderer_without_model_calls(tmp_path):
    request = BriefRequest(
        document_id="brief-job-001",
        locale="en-US",
        brief="""title: March invoice
customer: Acme Co
date: 2026-09-22
currency: USD
item: Hosting | 2 | 100.50 | 7
""",
    )

    result = run_document_brief_job(request, tmp_path, "brief-job-001")

    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.metadata["input_mode"] == "brief"
    assert result.metadata["extraction"]["adapter"] == "rule-based"
    assert request.brief not in json.dumps(result.to_dict(), ensure_ascii=False)
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["metadata"]["input_mode"] == "brief"
    assert request.brief not in json.dumps(manifest, ensure_ascii=False)


def test_brief_job_returns_needs_input_without_manifest(tmp_path):
    result = run_document_brief_job(
        {"document_id": "brief-missing", "brief": "title: Missing fields"},
        tmp_path,
        "brief-missing",
    )

    assert result.status == "needs_input"
    assert result.error_code == "needs_input"
    assert result.manifest_path is None
    assert "customer_name" in result.metadata["extraction"]["missing"]
    assert not list(tmp_path.rglob("manifest.json"))


def test_changed_brief_with_same_key_conflicts_even_if_fields_overlap(tmp_path):
    first = run_document_brief_job(
        {"document_id": "brief-conflict", "brief": "title: One\ncustomer: Acme\ndate: 2026-09-22\ncurrency: USD\nitem: Service | 1 | 10"},
        tmp_path,
        "brief-conflict",
    )
    assert first.status == "accepted"
    with pytest.raises(IdempotencyConflict):
        run_document_brief_job(
            {"document_id": "brief-conflict", "brief": "title: Two\ncustomer: Acme\ndate: 2026-09-22\ncurrency: USD\nitem: Service | 1 | 10"},
            tmp_path,
            "brief-conflict",
        )


class CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, brief: str, *, locale: str, template_id: str = "invoice", template_version: str = "1", budget=None) -> ExtractionResult:
        del brief, locale, template_id, template_version, budget
        self.calls += 1
        return ExtractionResult(
            status="complete",
            fields={
                "title": "Adapter invoice",
                "customer_name": "Synthetic adapter customer",
                "issue_date": "2026-09-22",
                "currency": "USD",
                "line_items": [{"description": "Service", "quantity": "1", "unit_price": "10.00", "tax_rate": "0"}],
            },
            provided=["title", "customer_name", "issue_date", "currency", "line_items"],
            model_calls=1,
            adapter="counting-test",
            input_tokens=20,
            output_tokens=40,
        )


def test_brief_replay_does_not_call_extraction_adapter_again(tmp_path):
    adapter = CountingAdapter()
    request = BriefRequest(document_id="brief-adapter", brief="title: Need adapter")

    first = run_document_brief_job(request, tmp_path, "brief-adapter", adapter=adapter)
    second = run_document_brief_job(request, tmp_path, "brief-adapter", adapter=adapter)

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert second.cost_event["replayed"] is True
    assert adapter.calls == 1
