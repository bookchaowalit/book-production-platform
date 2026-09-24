import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from book_production_platform.graphics_production import jobs
from book_production_platform.graphics_production.jobs import (
    IdempotencyConflict,
    run_batch,
)


def item(request_id: str = "quote-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "template_id": "quote_card",
        "variant": "square",
        "canvas": {"width": 1080, "height": 1080},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"quote": "ทำให้ชัดเจน", "author": "ทีมงาน"},
    }


def test_run_batch_writes_manifest_and_two_artifacts(tmp_path: Path) -> None:
    result = run_batch([item()], tmp_path, "batch-1")

    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.fresh_count == 1
    assert result.replay_count == 0
    assert len(result.items) == 1
    assert {artifact.media_type for artifact in result.items[0].artifacts} == {"image/svg+xml", "image/png"}
    assert result.manifest_path is not None and result.manifest_path.exists()
    assert json.loads(result.manifest_path.read_text(encoding="utf-8"))["telemetry"]["model_calls"] == 0


def test_run_batch_replays_without_rewriting_artifacts(tmp_path: Path) -> None:
    first = run_batch([item()], tmp_path, "batch-2")
    assert first.manifest_path is not None
    before = first.manifest_path.stat().st_mtime_ns

    second = run_batch([item()], tmp_path, "batch-2")

    assert second.status == "accepted"
    assert second.fresh_count == 0
    assert second.replay_count == 1
    assert second.bytes_written == 0
    assert second.manifest_path == first.manifest_path
    assert second.manifest_path.stat().st_mtime_ns == before


def test_run_batch_rejects_idempotency_reuse_with_different_input(tmp_path: Path) -> None:
    run_batch([item()], tmp_path, "batch-3")

    with pytest.raises(IdempotencyConflict):
        run_batch([item("different")], tmp_path, "batch-3")


def test_run_batch_keeps_malformed_item_as_actionable_rejection(tmp_path: Path) -> None:
    result = run_batch([item(), {"request_id": "bad", "template_id": "quote_card"}], tmp_path, "batch-4")

    assert result.status == "partial"
    assert [entry.status for entry in result.items] == ["accepted", "rejected"]
    assert result.items[1].error_code == "invalid_request"


def test_run_batch_rejects_replay_when_local_asset_bytes_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asset = tmp_path / "product.png"
    item_data = item()
    item_data.update({
        "template_id": "product_card",
        "content": {"title": "สินค้า", "price": "฿99"},
        "assets": {"product_image": asset.name},
        "asset_root": str(tmp_path),
    })

    def png_preview(_ir: object, *, asset_root: str | Path | None = None) -> bytes:
        del asset_root
        output = BytesIO()
        Image.new("RGB", (1080, 1080), "white").save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(jobs, "render_png", png_preview)
    first_asset = BytesIO()
    Image.new("RGB", (1, 1), "red").save(first_asset, format="PNG")
    asset.write_bytes(first_asset.getvalue())
    run_batch([item_data], tmp_path / "out", "asset-change")

    second_asset = BytesIO()
    Image.new("RGB", (1, 1), "blue").save(second_asset, format="PNG")
    asset.write_bytes(second_asset.getvalue())

    with pytest.raises(IdempotencyConflict):
        run_batch([item_data], tmp_path / "out", "asset-change")
