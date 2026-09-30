from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from book_production_platform.graphics_production import export as export_module
from book_production_platform.graphics_production.cli import main
from book_production_platform.graphics_production.export import (
    ExportError,
    export_batch,
)
from book_production_platform.graphics_production.jobs import run_batch


def _item(request_id: str = "quote-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "template_id": "quote_card",
        "variant": "square",
        "canvas": {"width": 1080, "height": 1080},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"quote": "ทำให้ชัดเจน", "author": "ทีมงาน"},
    }


@pytest.fixture(scope="module")
def batch(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("batch")
    result = run_batch([_item()], out, "export-source")
    assert result.status == "accepted"
    return next(out.glob("manifest-*.json"))


def test_export_writes_scaled_png_and_print_pdf(batch: Path, tmp_path: Path) -> None:
    result = export_batch(batch, tmp_path, scale=2, dpi=150)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "graphics-export.v1"
    assert manifest["source_manifest"]["content_hash"] == hashlib.sha256(batch.read_bytes()).hexdigest()
    png, pdf = manifest["items"][0]["artifacts"]
    png_bytes = (tmp_path / png["filename"]).read_bytes()
    assert Image.open(BytesIO(png_bytes)).size == (2160, 2160)
    assert png["content_hash"] == hashlib.sha256(png_bytes).hexdigest()
    assert pdf["media_type"] == "application/pdf"
    assert (tmp_path / pdf["filename"]).read_bytes().startswith(b"%PDF")
    assert pdf["print_size_mm"] == [365.8, 365.8]


def test_export_rejects_tampered_svg(batch: Path, tmp_path: Path) -> None:
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    for path in batch.parent.iterdir():
        (copy_dir / path.name).write_bytes(path.read_bytes())
    svg = next(copy_dir.glob("*.svg"))
    svg.write_text(svg.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")
    with pytest.raises(ExportError, match="does not match"):
        export_batch(copy_dir / batch.name, tmp_path / "out")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.pdf"))


@pytest.mark.parametrize("kwargs", [{"scale": 0}, {"scale": 7}, {"dpi": 50}, {"dpi": 5000}])
def test_export_rejects_out_of_range_settings(batch: Path, tmp_path: Path, kwargs: dict[str, int]) -> None:
    with pytest.raises(ExportError):
        export_batch(batch, tmp_path, **kwargs)


def test_export_rejects_non_batch_manifest(tmp_path: Path) -> None:
    other = tmp_path / "manifest.json"
    other.write_text(json.dumps({"schema_version": "spreadsheet-job.v1"}), encoding="utf-8")
    with pytest.raises(ExportError, match="graphics-batch.v1"):
        export_batch(other, tmp_path / "out")


def test_export_fails_clearly_without_browser(batch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_module, "_find_browser", lambda: None)
    assert main(["export", "--manifest", str(batch), "--output", str(tmp_path)]) == 2


def test_cli_export(batch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export", "--manifest", str(batch), "--output", str(tmp_path), "--scale", "1", "--dpi", "72"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == 1
