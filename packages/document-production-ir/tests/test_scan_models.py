import pytest
from document_ir import ScanImportRequest
from pydantic import ValidationError


def test_scan_import_request_round_trips_versioned_relative_source():
    request = ScanImportRequest.model_validate(
        {"document_id": "scan-001", "source_pdf": "inbox/invoice.pdf", "locale": "en-US"}
    )

    assert request.schema_version == "scan-import-request.v1"
    assert request.model_dump(mode="json")["source_pdf"] == "inbox/invoice.pdf"


def test_scan_import_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ScanImportRequest.model_validate(
            {"document_id": "scan-001", "source_pdf": "invoice.pdf", "unsafe": True}
        )


def test_scan_import_request_accepts_source_file_without_source_pdf():
    request = ScanImportRequest.model_validate({"document_id": "scan-002", "source_file": "inbox/invoice.docx"})

    assert request.schema_version == "scan-import-request.v1"
    assert request.source_pdf is None
    assert request.source_path == "inbox/invoice.docx"
    assert request.source_field == "source_file"


def test_scan_import_request_legacy_source_pdf_still_resolves_its_path():
    request = ScanImportRequest.model_validate({"document_id": "scan-003", "source_pdf": "invoice.pdf"})

    assert request.source_path == "invoice.pdf"
    assert request.source_field == "source_pdf"


@pytest.mark.parametrize(
    "payload",
    [
        {"document_id": "scan-004"},
        {"document_id": "scan-004", "source_pdf": "a.pdf", "source_file": "a.png"},
        {"document_id": "scan-004", "source_file": ""},
    ],
)
def test_scan_import_request_requires_exactly_one_source(payload):
    with pytest.raises(ValidationError):
        ScanImportRequest.model_validate(payload)
