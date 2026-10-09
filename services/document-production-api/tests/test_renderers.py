
import pytest
from document_production.qa import qa_document
from document_production.recipe import RequestValidationError, build_document_ir
from document_production.renderers import RenderError, _pdf_value, render_document
from test_recipe import sample_request


def test_renderer_writes_openable_docx_and_pdf(tmp_path):
    artifacts = render_document(build_document_ir(sample_request()), tmp_path)
    assert {artifact.media_type for artifact in artifacts} == {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf",
    }
    assert all((tmp_path / artifact.filename).stat().st_size > 0 for artifact in artifacts)
    assert (tmp_path / "document.docx").read_bytes()[:2] == b"PK"
    assert (tmp_path / "document.pdf").read_bytes()[:5] == b"%PDF-"


def test_renderer_rejects_missing_template(tmp_path):
    ir = build_document_ir(sample_request())
    ir = ir.model_copy(update={"template_ref": "missing@1"})
    with pytest.raises(RenderError, match="template"):
        render_document(ir, tmp_path)


def test_pdf_values_escape_markup_instead_of_interpreting_it():
    assert _pdf_value("<safe>&", "Helvetica") == "&lt;safe&gt;&amp;"


def test_pdf_values_reject_unicode_without_a_compatible_font():
    with pytest.raises(RenderError, match="font"):
        _pdf_value("ภาษาไทย", "Helvetica")


def test_renderer_rejects_unicode_when_font_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("document_production.renderers._font_name", lambda: "Helvetica")
    with pytest.raises(RenderError, match="font"):
        render_document(build_document_ir(sample_request(title="ภาษาไทย")), tmp_path)


def test_renderer_handles_one_hundred_line_items_within_page_limit(tmp_path):
    request = sample_request(line_items=[{"description": f"Item {index}", "quantity": "1", "unit_price": "1", "tax_rate": "0"} for index in range(100)])
    artifacts = render_document(build_document_ir(request), tmp_path)
    pdf = next(artifact for artifact in artifacts if artifact.media_type == "application/pdf")
    assert pdf.page_count is not None and pdf.page_count <= 10


def test_zero_line_items_is_rejected_before_rendering():
    with pytest.raises(RequestValidationError, match="line_items_required"):
        build_document_ir(sample_request(line_items=[]))


def test_single_line_item_document_passes_qa(tmp_path):
    request = sample_request(
        document_id="single-item",
        line_items=[{"description": "Only item", "quantity": "2", "unit_price": "12.50", "tax_rate": "7"}],
    )
    ir = build_document_ir(request)
    artifacts = render_document(ir, tmp_path)
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "passed"
    assert result.issues == []


def test_one_hundred_one_line_items_is_rejected_before_rendering():
    request = sample_request(line_items=[{"description": f"Item {index}", "quantity": "1", "unit_price": "1", "tax_rate": "0"} for index in range(101)])
    with pytest.raises(RequestValidationError, match="line_item_limit_exceeded"):
        build_document_ir(request)


def test_hundred_line_items_spans_multiple_pages_with_accurate_page_count(tmp_path):
    request = sample_request(
        document_id="multi-page",
        line_items=[{"description": f"Item {index}", "quantity": "1", "unit_price": "1", "tax_rate": "0"} for index in range(100)],
    )
    ir = build_document_ir(request)
    artifacts = render_document(ir, tmp_path)
    pdf = next(artifact for artifact in artifacts if artifact.media_type == "application/pdf")
    assert pdf.page_count is not None and 2 <= pdf.page_count <= 10
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "passed"
    assert result.page_count == pdf.page_count


def test_long_wrapping_text_stays_within_page_limit_and_passes_qa(tmp_path):
    long_description = "Long service description line that must wrap inside the table cell " * 20
    request = sample_request(
        document_id="long-text",
        line_items=[{"description": long_description, "quantity": "1", "unit_price": "1", "tax_rate": "0"}],
    )
    ir = build_document_ir(request)
    artifacts = render_document(ir, tmp_path)
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "passed"
    pdf = next(artifact for artifact in artifacts if artifact.media_type == "application/pdf")
    assert pdf.page_count is not None and pdf.page_count <= 10


def test_thai_text_with_vowels_and_tones_renders_or_fails_loudly(tmp_path):
    request = sample_request(
        document_id="thai-doc",
        title="ใบแจ้งหนี้ ค่าบริการ",
        customer_name="บริษัท ทดสอบ จำกัด",
        line_items=[{"description": "ค่าบริการ โฮสติ้ง เดือนกันยายน", "quantity": "3", "unit_price": "150.25", "tax_rate": "7"}],
    )
    ir = build_document_ir(request)
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
