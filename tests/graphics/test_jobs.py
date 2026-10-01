import hashlib
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
from tests.graphics.browser import requires_browser


def item(request_id: str = "quote-001") -> dict[str, object]:
    return {
        "request_id": request_id,
        "template_id": "quote_card",
        "variant": "square",
        "canvas": {"width": 1080, "height": 1080},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"quote": "ทำให้ชัดเจน", "author": "ทีมงาน"},
    }


@requires_browser
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


@requires_browser
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


@requires_browser
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


def _stub_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace browser rasterization with a canvas-sized PNG so naming logic runs anywhere."""

    def fake_render_png(ir: object, *, asset_root: object = None) -> bytes:
        canvas = ir.canvas  # type: ignore[attr-defined]
        buffer = BytesIO()
        Image.new("RGB", (canvas.width, canvas.height), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(jobs, "render_png", fake_render_png)
    monkeypatch.setattr(jobs, "run_graphic_qa", lambda svg, png, ir: ())


@pytest.mark.parametrize("second_id", ["quote-001", "QUOTE-001", "..quote-001", "quote-001.."])
def test_run_batch_rejects_items_whose_artifact_names_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_id: str
) -> None:
    _stub_browser(monkeypatch)
    first = item("quote-001")
    second = {**item(second_id), "content": {"quote": "อีกข้อความ", "author": "คนอื่น"}}

    result = run_batch([first, second], tmp_path, f"collision-{second_id}")

    assert result.status == "partial"
    assert [entry.status for entry in result.items] == ["accepted", "rejected"]
    assert result.items[1].error_code == "artifact_name_collision"
    svg_artifact = next(a for a in result.items[0].artifacts if a.media_type == "image/svg+xml")
    written = (tmp_path / svg_artifact.filename).read_bytes()
    # The accepted item's file still matches the hash recorded in the manifest.
    assert hashlib.sha256(written).hexdigest() == svg_artifact.content_hash
    assert "ทำให้ชัดเจน".encode() in written


def test_run_batch_reuses_a_name_whose_first_owner_was_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_browser(monkeypatch)
    broken = {**item("quote-001"), "template_id": "missing_template"}

    result = run_batch([broken, item("quote-001")], tmp_path, "reuse-after-reject")

    assert [entry.status for entry in result.items] == ["rejected", "accepted"]
    assert result.status == "partial"


def test_later_batch_reusing_a_request_id_gets_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_browser(monkeypatch)
    first = run_batch([item("quote-001")], tmp_path, "first-batch")
    changed = {**item("quote-001"), "content": {"quote": "ข้อความใหม่", "author": "คนอื่น"}}

    second = run_batch([changed], tmp_path, "second-batch")

    assert second.status == "accepted"
    first_names = {artifact.filename for artifact in first.items[0].artifacts}
    second_names = {artifact.filename for artifact in second.items[0].artifacts}
    assert first_names == {f"{first.batch_id}/quote-001.svg", f"{first.batch_id}/quote-001.png"}
    assert second_names == {f"{second.batch_id}/quote-001.svg", f"{second.batch_id}/quote-001.png"}
    # Every file either manifest records still has the recorded hash.
    for result in (first, second):
        assert result.manifest_path is not None
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest["items"][0]["artifacts"]:
            data = (tmp_path / artifact["filename"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == artifact["content_hash"]


def test_non_deterministic_png_bytes_no_longer_reject_a_later_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_browser(monkeypatch)
    counter = iter(range(1, 100))

    def jittery_png(ir: object, *, asset_root: object = None) -> bytes:
        buffer = BytesIO()
        Image.new("RGB", (4, 4), (next(counter), 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(jobs, "render_png", jittery_png)
    first = run_batch([item("quote-001")], tmp_path, "first-batch")
    second = run_batch([item("quote-001")], tmp_path, "another-key")

    assert first.status == second.status == "accepted"
    first_png = next(a for a in first.items[0].artifacts if a.media_type == "image/png")
    second_png = next(a for a in second.items[0].artifacts if a.media_type == "image/png")
    assert first_png.content_hash != second_png.content_hash
    assert hashlib.sha256((tmp_path / first_png.filename).read_bytes()).hexdigest() == first_png.content_hash


def test_leftover_folder_with_different_bytes_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_browser(monkeypatch)
    first = run_batch([item("quote-001")], tmp_path, "first-batch")
    assert first.manifest_path is not None
    svg = next(a for a in first.items[0].artifacts if a.media_type == "image/svg+xml")
    (tmp_path / svg.filename).write_bytes(b"<svg>edited by hand</svg>")
    first.manifest_path.unlink()

    rerun = run_batch([item("quote-001")], tmp_path, "first-batch")

    assert rerun.status == "rejected"
    assert rerun.items[0].error_code == "artifact_exists_with_different_content"
    assert (tmp_path / svg.filename).read_bytes() == b"<svg>edited by hand</svg>"


def test_non_object_items_are_rejected_per_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_browser(monkeypatch)

    result = run_batch([item("quote-001"), "not-an-object", 7], tmp_path, "mixed")  # type: ignore[list-item]

    assert [entry.status for entry in result.items] == ["accepted", "rejected", "rejected"]
    assert [entry.error_code for entry in result.items[1:]] == ["invalid_request", "invalid_request"]
    assert result.status == "partial"


@pytest.mark.parametrize("request_id", ["CON", "nul", "Com1", "lpt9.card", "aux"])
def test_safe_name_never_produces_a_windows_reserved_device_stem(request_id: str) -> None:
    name = jobs._safe_name(request_id, "item-1")
    assert name.split(".", 1)[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT9"}
    assert name.endswith(request_id)


def test_safe_name_keeps_ordinary_ids_and_falls_back_for_empty_ones() -> None:
    assert jobs._safe_name("quote-001", "item-1") == "quote-001"
    assert jobs._safe_name("CONSOLE", "item-1") == "CONSOLE"
    assert jobs._safe_name("ข้อความ", "item-3") == "item-3"
