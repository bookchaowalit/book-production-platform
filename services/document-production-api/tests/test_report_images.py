"""Embedding verified PNG charts into report@1 (Production Hub E5)."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from docx import Document
from PIL import Image
from pypdf import PdfReader
from test_report import GOLDEN_REQUEST

from document_production.cli import main
from document_production.jobs import run_report_job
from document_production.recipe import RequestValidationError
from document_production.report import build_report_ir


def _png(path: Path, color: tuple[int, int, int] = (37, 99, 235)) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 240), color).save(buffer, format="PNG")
    data = buffer.getvalue()
    path.write_bytes(data)
    return {"name": path.name, "media_type": "image/png", "content_hash": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data)}


def _request(charts: list[dict]) -> dict:
    return {**json.loads(json.dumps(GOLDEN_REQUEST)), "charts": charts}


def test_png_chart_is_embedded_in_docx_and_pdf(tmp_path: Path) -> None:
    ref = _png(tmp_path / "chart.png")
    result = run_report_job(_request([ref, GOLDEN_REQUEST["charts"][0]]), tmp_path / "out", "embed-1",
                            chart_root=tmp_path)
    assert result.status == "accepted", result.qa
    job_dir = Path(result.manifest_path).parent
    assert len(Document(str(job_dir / "document.docx")).inline_shapes) == 1
    pdf_images = sum(len(page.images) for page in PdfReader(str(job_dir / "document.pdf")).pages)
    assert pdf_images == 1


def test_without_chart_root_charts_stay_text_references(tmp_path: Path) -> None:
    ref = _png(tmp_path / "chart.png")
    ir = build_report_ir(_request([ref]))
    assert ir.sections[-1].images == []
    assert ir.assumptions["embedded_chart_count"] == "0"


def test_embedding_changes_the_request_hash(tmp_path: Path) -> None:
    ref = _png(tmp_path / "chart.png")
    plain = run_report_job(_request([ref]), tmp_path / "out", "same-key-a")
    embedded = run_report_job(_request([ref]), tmp_path / "out", "same-key-b", chart_root=tmp_path)
    assert plain.status == embedded.status == "accepted"
    plain_manifest = json.loads(Path(plain.manifest_path).read_text(encoding="utf-8"))
    embedded_manifest = json.loads(Path(embedded.manifest_path).read_text(encoding="utf-8"))
    assert plain_manifest["request_hash"] != embedded_manifest["request_hash"]


@pytest.mark.parametrize("problem", ["missing", "tampered"])
def test_missing_or_mismatched_chart_is_rejected(tmp_path: Path, problem: str) -> None:
    ref = _png(tmp_path / "chart.png")
    if problem == "missing":
        (tmp_path / "chart.png").unlink()
    else:
        _png(tmp_path / "chart.png", color=(255, 0, 0))
    with pytest.raises(RequestValidationError) as caught:
        build_report_ir(_request([ref]), chart_root=tmp_path)
    assert caught.value.issues[0].code == ("chart_file_missing" if problem == "missing" else "chart_file_mismatch")
    result = run_report_job(_request([ref]), tmp_path / "out", f"bad-{problem}", chart_root=tmp_path)
    assert result.status == "failed"


def test_cli_report_chart_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = _png(tmp_path / "chart.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(_request([ref]), ensure_ascii=False), encoding="utf-8")
    code = main(["report", "--input", str(request), "--output", str(tmp_path / "out"),
                 "--idempotency-key", "cli-embed", "--chart-root", str(tmp_path)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"
