from pathlib import Path

import pytest
from document_production.scan_import import (
    OCRFailure,
    TesseractOCRAdapter,
    run_scan_import,
)
from reportlab.pdfgen import canvas


def create_pdf(path: Path, pages: list[list[str]]) -> None:
    writer = canvas.Canvas(str(path), pagesize=(612, 792))
    for lines in pages:
        y = 740
        for line in lines:
            writer.drawString(40, y, line)
            y -= 24
        writer.showPage()
    writer.save()


def complete_fields() -> list[str]:
    return [
        "title: Synthetic invoice",
        "customer: Example customer",
        "date: 2026-09-22",
        "currency: USD",
        "item: Design service | 2 | 10.00 | 0",
    ]


def test_embedded_pdf_text_produces_field_proposals_with_page_provenance(tmp_path):
    source = tmp_path / "invoice.pdf"
    create_pdf(source, [complete_fields()])

    result = run_scan_import(
        {"document_id": "scan-embedded", "source_pdf": "invoice.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "complete"
    assert result.model_calls == 0
    assert result.manifest.ocr_pages_used == 0
    assert result.fields["customer_name"].value == "Example customer"
    assert result.fields["customer_name"].evidence[0].page_number == 1
    assert result.fields["customer_name"].evidence[0].method == "embedded_text"
    assert result.fields["customer_name"].evidence[0].source_excerpt == "customer: Example customer"
    assert result.qa.field_provenance_complete is True


def test_ocr_runs_only_for_low_text_pages_and_combines_page_proposals(tmp_path):
    source = tmp_path / "two-pages.pdf"
    create_pdf(source, [complete_fields()[:-1], []])

    class FakeOCR:
        def __init__(self):
            self.calls: list[tuple[Path, int, str]] = []

        def extract_page(self, pdf_path: Path, page_number: int, *, locale: str) -> str:
            self.calls.append((pdf_path, page_number, locale))
            return "item: Design service | 2 | 10.00 | 0"

    ocr = FakeOCR()
    result = run_scan_import(
        {"document_id": "scan-ocr", "source_pdf": "two-pages.pdf", "locale": "en-US"},
        input_root=tmp_path,
        ocr_adapter=ocr,
    )

    assert result.status == "complete"
    assert [page for _, page, _ in ocr.calls] == [2]
    assert ocr.calls[0][2] == "en-US"
    assert result.manifest.ocr_pages_used == 1
    assert result.fields["line_items"].evidence[0].page_number == 2
    assert result.fields["line_items"].evidence[0].method == "ocr"


def test_scan_abstains_when_a_page_requires_unavailable_ocr(tmp_path, monkeypatch):
    source = tmp_path / "low-text.pdf"
    create_pdf(source, [complete_fields()])
    monkeypatch.setattr("document_production.scan_import.MIN_EMBEDDED_TEXT_CHARS", 10_000)

    result = run_scan_import(
        {"document_id": "scan-no-ocr", "source_pdf": "low-text.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "needs_input"
    assert result.model_calls == 0
    assert result.issues[0].code == "ocr_unavailable"
    assert result.fields["customer_name"].evidence[0].method == "embedded_text"


def test_scan_marks_conflicting_repeated_fields_ambiguous(tmp_path):
    source = tmp_path / "conflict.pdf"
    create_pdf(
        source,
        [
            ["title: First", "customer: Example customer", "date: 2026-09-22", "currency: USD", "item: Service | 1 | 10"],
            ["title: Second"],
        ],
    )

    result = run_scan_import(
        {"document_id": "scan-conflict", "source_pdf": "conflict.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "needs_input"
    assert result.fields["title"].status == "ambiguous"
    assert result.fields["title"].value is None
    assert result.fields["title"].candidates == ["First", "Second"]
    assert "title" in result.ambiguous_fields
    assert result.qa.no_ambiguous_fields is False


def test_scan_marks_duplicate_labels_on_one_page_ambiguous(tmp_path):
    source = tmp_path / "duplicate.pdf"
    create_pdf(
        source,
        [["title: First", "title: Second", "customer: Example", "date: 2026-09-22", "currency: USD", "item: Service | 1 | 10"]],
    )

    result = run_scan_import(
        {"document_id": "scan-duplicate", "source_pdf": "duplicate.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "needs_input"
    assert result.fields["title"].status == "ambiguous"
    assert len(result.fields["title"].evidence) == 2


def test_scan_rejects_malformed_pdf_without_exposing_parser_details(tmp_path):
    (tmp_path / "invalid.pdf").write_bytes(b"not a PDF")

    result = run_scan_import(
        {"document_id": "scan-invalid", "source_pdf": "invalid.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "rejected"
    assert result.issues[0].code == "pdf_invalid"
    assert "not a PDF" not in result.issues[0].message


def test_scan_rejects_pdf_over_page_limit(tmp_path):
    source = tmp_path / "too-many-pages.pdf"
    create_pdf(source, [[] for _ in range(51)])

    result = run_scan_import(
        {"document_id": "scan-pages", "source_pdf": "too-many-pages.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "rejected"
    assert result.issues[0].code == "pdf_page_limit_exceeded"


def test_ocr_language_mapping_is_allowlisted_before_running_tools(tmp_path, monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("an unsupported locale must not invoke a process")

    monkeypatch.setattr("document_production.scan_import.subprocess.run", unexpected_run)

    with pytest.raises(OCRFailure):
        TesseractOCRAdapter("pdftoppm", "tesseract").extract_page(tmp_path / "scan.pdf", 1, locale="en;whoami")


def test_scan_enforces_a_per_document_ocr_page_budget(tmp_path):
    source = tmp_path / "many-scans.pdf"
    create_pdf(source, [[] for _ in range(11)])

    class CountingOCR:
        def __init__(self):
            self.calls = 0

        def extract_page(self, pdf_path: Path, page_number: int, *, locale: str) -> str:
            del pdf_path, page_number, locale
            self.calls += 1
            return ""

    ocr = CountingOCR()
    result = run_scan_import(
        {"document_id": "scan-budget", "source_pdf": "many-scans.pdf", "locale": "en-US"},
        input_root=tmp_path,
        ocr_adapter=ocr,
    )

    assert result.status == "needs_input"
    assert ocr.calls == 10
    assert result.manifest.ocr_pages_used == 10
    assert result.issues[-1].code == "ocr_page_limit_exceeded"


def test_scan_rejects_source_path_outside_input_root(tmp_path):
    outside = tmp_path.parent / "outside.pdf"
    create_pdf(outside, [complete_fields()])

    result = run_scan_import(
        {"document_id": "scan-path", "source_pdf": "../outside.pdf", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "rejected"
    assert result.issues[0].code == "source_path_invalid"
