from document_production.qa import qa_document
from document_production.recipe import build_document_ir
from document_production.renderers import _artifact, render_document
from docx import Document
from test_recipe import sample_request


def test_qa_accepts_matching_artifacts(tmp_path):
    ir = build_document_ir(sample_request())
    artifacts = render_document(ir, tmp_path)
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "passed"
    assert result.issues == []


def test_qa_rejects_tampered_artifact(tmp_path):
    ir = build_document_ir(sample_request())
    artifacts = render_document(ir, tmp_path)
    (tmp_path / "document.pdf").write_bytes(b"tampered")
    result = qa_document(ir, artifacts, tmp_path)
    assert result.status == "failed"
    assert any(issue["code"] == "artifact_hash_mismatch" for issue in result.issues)


def test_qa_rejects_missing_source_text_in_docx(tmp_path):
    ir = build_document_ir(sample_request())
    artifacts = render_document(ir, tmp_path)
    docx = Document(tmp_path / "document.docx")
    docx.paragraphs[0].text = ""
    docx.save(tmp_path / "document.docx")
    tampered_docx = _artifact(
        tmp_path / "document.docx",
        "document.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    result = qa_document(ir, [tampered_docx, artifacts[1]], tmp_path)
    assert result.status == "failed"
    assert any(issue["code"] == "document_text_missing" for issue in result.issues)
