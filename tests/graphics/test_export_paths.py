"""Browser-free checks of how export resolves artifact paths from a batch manifest."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from book_production_platform.graphics_production import export as export_module
from book_production_platform.graphics_production import jobs
from book_production_platform.graphics_production.export import (
    ExportError,
    export_batch,
)
from book_production_platform.graphics_production.jobs import run_batch
from tests.graphics.test_jobs import item


@pytest.mark.parametrize("filename", ["../outside.svg", "/etc/card.svg", "a/b/c.svg", "C:card.svg", "batch/..\\card.svg"])
def test_export_rejects_unsafe_artifact_paths_before_rendering(tmp_path: Path, filename: str) -> None:
    manifest = tmp_path / "manifest-x.json"
    manifest.write_text(json.dumps({
        "schema_version": "graphics-batch.v1",
        "status": "accepted",
        "batch_id": "b",
        "items": [{"request_id": "r", "status": "accepted", "artifacts": [
            {"filename": filename, "media_type": "image/svg+xml", "content_hash": "0" * 64, "width": 1, "height": 1},
        ]}],
    }), encoding="utf-8")

    with pytest.raises(ExportError, match="unsafe artifact path"):
        export_batch(manifest, tmp_path / "out")


def _png(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_export_reads_svgs_from_the_batch_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "render_png", lambda ir, *, asset_root=None: _png(ir.canvas.width, ir.canvas.height))
    monkeypatch.setattr(jobs, "run_graphic_qa", lambda svg, png, ir: ())
    monkeypatch.setattr(export_module, "_render_scaled", lambda path, width, height, scale: _png(width * scale, height * scale))
    batch = run_batch([item("quote-001")], tmp_path / "batch", "export-paths")
    assert batch.manifest_path is not None

    result = export_batch(batch.manifest_path, tmp_path / "out", scale=1, dpi=150)

    exported = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert exported["items"][0]["source_svg"]["filename"] == f"{batch.batch_id}/quote-001.svg"
    assert sorted(path.name for path in (tmp_path / "out").iterdir() if path.suffix != ".json") == [
        "quote-001-print.pdf",
        "quote-001@1x.png",
    ]
