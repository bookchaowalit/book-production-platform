from pathlib import Path
from typing import ClassVar

import pytest
from document_production import renderers
from document_production.qa import qa_document
from document_production.recipe import RequestValidationError, build_document_ir
from document_production.renderers import RenderError, _missing_codepoints, _pdf_value, render_document
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


THAI_ONLY = {*range(0x0E01, 0x0E3B), *range(0x0E3F, 0x0E5C)}
LATIN_AND_THAI = {*range(0x20, 0x7F), *THAI_ONLY}
THAI_CONSONANTS_ONLY = {*range(0x20, 0x7F), *range(0x0E01, 0x0E2F)}


def _cmap(codepoints, glyph=1):
    return dict.fromkeys(codepoints, glyph)


class _FakeTTFont:
    """Stands in for reportlab's TTFont, keyed by path to a cmap."""

    cmaps: ClassVar[dict[str, set[int]]] = {}

    def __init__(self, name, path):
        self.fontName = name
        self.face = type("Face", (), {"charToGlyph": {codepoint: 1 for codepoint in self.cmaps[path]}})()


@pytest.fixture
def font_selection(monkeypatch, tmp_path):
    """Run _font_name against fake font files without touching reportlab's global registry."""

    registered = []
    monkeypatch.setattr(renderers.pdfmetrics, "getRegisteredFontNames", list)
    monkeypatch.setattr(renderers.pdfmetrics, "registerFont", registered.append)
    monkeypatch.setattr(renderers, "TTFont", _FakeTTFont)
    monkeypatch.delenv("DOCUMENT_PRODUCTION_FONT", raising=False)

    def install(cmaps):
        paths = []
        _FakeTTFont.cmaps = {}
        for name, cmap in cmaps.items():
            path = tmp_path / name
            path.write_bytes(b"")
            _FakeTTFont.cmaps[str(path)] = cmap
            paths.append(str(path))
        monkeypatch.setattr(renderers, "PDF_FONT_CANDIDATES", tuple(paths))
        return paths

    return install, registered


def test_thai_only_font_is_missing_latin_and_digits():
    missing = {chr(codepoint) for codepoint in _missing_codepoints(_cmap(THAI_ONLY))}
    assert set("0123456789AZaz/.") <= missing
    assert _missing_codepoints(_cmap(LATIN_AND_THAI)) == []


def test_font_without_thai_vowels_and_tone_marks_is_incomplete():
    missing = {chr(codepoint) for codepoint in _missing_codepoints(_cmap(THAI_CONSONANTS_ONLY))}
    assert set("\u0e34\u0e48\u0e3f\u0e51") <= missing  # sara i, mai ek, Baht sign, Thai digit one


def test_codepoints_mapped_to_notdef_count_as_missing():
    cmap = {**_cmap(LATIN_AND_THAI), ord("2"): 0}
    assert _missing_codepoints(cmap) == [ord("2")]


def test_fallback_skips_a_thai_only_font_for_a_latin_capable_one(font_selection):
    install, registered = font_selection
    install({"NotoSansThai-Regular.ttf": THAI_ONLY, "Garuda.ttf": LATIN_AND_THAI})
    assert renderers._font_name() == renderers.PDF_FONT_NAME
    assert [set(font.face.charToGlyph) for font in registered] == [LATIN_AND_THAI]


def test_fallback_uses_helvetica_when_only_thai_only_fonts_exist(font_selection):
    install, registered = font_selection
    install({"NotoSansThai-Regular.ttf": THAI_ONLY})
    assert renderers._font_name() == "Helvetica"
    assert registered == []


def test_configured_font_without_latin_glyphs_fails_loudly(font_selection, monkeypatch):
    install, registered = font_selection
    [thai_only] = install({"NotoSansThai-Regular.ttf": THAI_ONLY})
    monkeypatch.setenv("DOCUMENT_PRODUCTION_FONT", thai_only)
    with pytest.raises(RenderError, match="lacks .* required glyphs"):
        renderers._font_name()
    assert registered == []


def test_configured_font_that_does_not_exist_fails_loudly(font_selection, monkeypatch, tmp_path):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_FONT", str(tmp_path / "missing.ttf"))
    with pytest.raises(RenderError, match="does not exist"):
        renderers._font_name()


@pytest.mark.parametrize(
    ("path", "covers"),
    [
        ("/usr/share/fonts/truetype/tlwg/Garuda.ttf", True),
        ("/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf", False),
    ],
)
def test_real_thai_fonts_glyph_coverage(path, covers):
    if not Path(path).exists():
        pytest.skip(f"{path} is not installed")
    from reportlab.pdfbase.ttfonts import TTFont

    assert (_missing_codepoints(TTFont("CoverageProbe", path).face.charToGlyph) == []) is covers
