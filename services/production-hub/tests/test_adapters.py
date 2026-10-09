from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from production_hub import adapters
from production_hub.adapters import (
    GAME_AUDIO_ROLES,
    GRAPHICS_VARIANTS,
    MARKETING_CHANNELS,
    SCENE_COMPOSITIONS,
    SCENE_RENDER_PROFILES,
)
from production_hub.hub import Hub
from production_hub.ingest import ingest_manifest
from production_hub.send import SEND_ADAPTERS, Materialized, SendContext, SendError


def _ctx(tmp_path: Path, name: str, content: bytes, kind: str, params: dict[str, str],
         brand: dict[str, Any] | None = None) -> SendContext:
    inputs = tmp_path / "work" / "inputs"
    inputs.mkdir(parents=True)
    path = inputs / name
    path.write_bytes(content)
    item = Materialized("sha256:" + hashlib.sha256(content).hexdigest(), kind, path, {})
    return SendContext(tmp_path / "work", [item], params, "20260930T000000-abcd1234", brand)


def _request(run_argv: list[str]) -> dict[str, Any]:
    path = Path(run_argv[run_argv.index("--input") + 1])
    request: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return request


def test_adapters_registered_for_implemented_edges() -> None:
    assert {"E4-chart-to-graphics", "E6-copy-to-graphics"} <= set(SEND_ADAPTERS)


def test_optional_params_are_declared_for_the_console() -> None:
    e4 = SEND_ADAPTERS["E4-chart-to-graphics"]
    assert e4.optional_params["variants"].choices == tuple(GRAPHICS_VARIANTS)
    e2 = SEND_ADAPTERS["E2-captions-to-marketing"]
    assert e2.optional_params["channels"].choices == MARKETING_CHANNELS
    assert e2.optional_params["channels"].default == "facebook,instagram"
    e9 = SEND_ADAPTERS["E9-cad-to-scene"]
    assert e9.optional_params["render_profile"].choices == SCENE_RENDER_PROFILES
    assert e9.optional_params["composition"].default == SCENE_COMPOSITIONS[0]
    e12 = SEND_ADAPTERS["E12b-audio-to-game"]
    assert e12.optional_params["role"].choices == GAME_AUDIO_ROLES
    assert SEND_ADAPTERS["E7b-graphic-to-web"].optional_params["body"].description


def test_chart_to_graphics_builds_product_card_per_variant(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "abc-chart.svg", b"<svg/>", "chart.svg",
               {"title": "ยอดขาย", "caption": "รวม 100 บาท", "variants": "square,story", "primary": "#000000"})
    run = adapters.build_chart_to_graphics(ctx)
    items = _request(list(run.argv))["items"]
    assert [item["variant"] for item in items] == ["square", "story"]
    assert items[1]["canvas"] == {"width": 1080, "height": 1920}
    assert items[0]["template_id"] == "product_card"
    assert items[0]["assets"] == {"product_image": "abc-chart.svg"}
    assert Path(items[0]["asset_root"]) == ctx.artifacts[0].path.parent
    assert items[0]["brand"]["primary"] == "#000000"
    key = run.argv[run.argv.index("--idempotency-key") + 1]
    expected = f"manifest-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"
    assert run.manifest_path().name == expected
    assert "book_production_platform.cli" in run.argv


def test_brand_kit_colors_feed_graphics_brand(tmp_path: Path) -> None:
    kit = {"colors": {"primary": "#0A7CFF", "secondary": "#FF00AA", "text": "#222222", "background": "#FAFAFA"}}
    ctx = _ctx(tmp_path, "c.svg", b"<svg/>", "chart.svg", {"title": "t", "caption": "c"}, brand=kit)
    brand = _request(list(adapters.build_chart_to_graphics(ctx).argv))["items"][0]["brand"]
    assert brand == {"primary": "#0A7CFF", "accent": "#FF00AA", "foreground": "#222222", "background": "#FAFAFA"}


def test_bad_variant_is_rejected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "c.svg", b"<svg/>", "chart.svg", {"title": "t", "caption": "c", "variants": "banner"})
    with pytest.raises(SendError, match="variants"):
        adapters.build_chart_to_graphics(ctx)


def test_copy_to_graphics_announcement_and_quote(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "copy.txt", "ส่งไวภายใน 3 วัน".encode(), "copy.text", {"headline": "ลายใหม่"})
    item = _request(list(adapters.build_copy_to_graphics(ctx).argv))["items"][0]
    assert item["template_id"] == "announcement"
    assert item["content"] == {"headline": "ลายใหม่", "body": "ส่งไวภายใน 3 วัน", "cta": "ดูเพิ่มเติม"}

    quote_ctx = _ctx(tmp_path / "q", "copy.txt", b"Stay bold", "copy.text",
                     {"headline": "Shop A", "template": "quote_card"})
    quote = _request(list(adapters.build_copy_to_graphics(quote_ctx).argv))["items"][0]
    assert quote["content"] == {"quote": "Stay bold", "author": "Shop A"}


def test_empty_copy_is_rejected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "copy.txt", b"   ", "copy.text", {"headline": "x"})
    with pytest.raises(SendError, match="empty"):
        adapters.build_copy_to_graphics(ctx)


def test_campaign_manifest_splits_copy_into_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    texts = {"copy-fb": "ลายใหม่มาแล้ว", "copy-ig": "ใส่สบาย"}
    package = {"campaign": {"campaign_id": "c1", "assets": [
        {"asset_id": "copy-fb", "kind": "copy", "channel": "facebook", "text": texts["copy-fb"]},
        {"asset_id": "copy-ig", "kind": "copy", "channel": "instagram", "text": texts["copy-ig"]},
        {"asset_id": "img-1", "kind": "image", "channel": "instagram", "uri": "local:img.png"},
    ]}}
    package_bytes = json.dumps(package, ensure_ascii=False).encode()
    (out / "c1.campaign.json").write_bytes(package_bytes)
    manifest = out / "manifest-1.json"
    manifest.write_text(json.dumps({
        "schema_version": "campaign-manifest.v1", "campaign_id": "c1", "idempotency_key": "k1", "status": "accepted",
        "metadata": {"contract_version": "marketing.v1a", "recipe_version": "campaign-package-v1"},
        "package": {"filename": "c1.campaign.json", "content_hash": hashlib.sha256(package_bytes).hexdigest()},
        "lineage": {"asset_hashes": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in texts.items()}},
    }), encoding="utf-8")
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        result = ingest_manifest(hub, conn, manifest)
    kinds = [item.kind for item in result.outputs]
    assert kinds == ["campaign.package", "copy.text", "copy.text"]
    with hub.db.connect() as conn:
        copy = hub.db.get(conn, result.outputs[1].artifact_id)
    assert copy is not None
    assert copy["lineage"] == [result.outputs[0].artifact_id]
    assert copy["attributes"]["channel"] == "facebook"
    assert hub.store.path_for(result.outputs[1].artifact_id).read_text(encoding="utf-8") == texts["copy-fb"]


def test_master_to_video_requires_one_video_and_builds_replace_audio(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "master.wav", b"RIFF master", "audio.master", {"name": "final"})
    with pytest.raises(SendError, match="exactly one video"):
        adapters.build_master_to_video(ctx)
    video = ctx.work_dir / "inputs" / "clip.mp4"
    video.write_bytes(b"mp4")
    with_video = SendContext(ctx.work_dir, [*ctx.artifacts, Materialized("sha256:" + "b" * 64, "video.source", video, {})],
                             ctx.params, ctx.send_id)
    run = adapters.build_master_to_video(with_video)
    assert run.argv[1:4] == ["-m", "video_postpipeline.cli", "replace-audio"]
    assert run.argv[run.argv.index("--video") + 1] == str(video)
    assert run.argv[run.argv.index("--remux-id") + 1] == "final"
    assert run.manifest_path().name == "video-remux.json"
    assert "video-postproduction" in run.env["PYTHONPATH"]


def test_video_remux_manifest_links_video_and_master(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    sources = tmp_path / "src"
    sources.mkdir()
    (sources / "clip.mp4").write_bytes(b"video bytes")
    (sources / "master.wav").write_bytes(b"master bytes")
    out = tmp_path / "out"
    out.mkdir()
    (out / "final.mp4").write_bytes(b"remuxed")
    manifest = out / "video-remux.json"
    manifest.write_text(json.dumps({
        "schema_version": "video-remux.v1", "remux_id": "final", "status": "accepted", "recipe": "replace-audio.v1",
        "source_video": {"filename": "clip.mp4", "sha256": hashlib.sha256(b"video bytes").hexdigest()},
        "source_audio": {"filename": "master.wav", "sha256": hashlib.sha256(b"master bytes").hexdigest()},
        "output": {"filename": "final.mp4", "media_type": "video/mp4",
                   "sha256": hashlib.sha256(b"remuxed").hexdigest(), "duration_seconds": 2.0},
        "qa": {"passed": True},
    }), encoding="utf-8")
    with hub.transaction() as conn:
        result = ingest_manifest(hub, conn, manifest, input_files=[sources / "clip.mp4", sources / "master.wav"])
        record = hub.db.get(conn, result.outputs[0].artifact_id)
    assert record is not None and record["kind"] == "video.render"
    assert sorted(record["lineage"]) == sorted(item.artifact_id for item in result.inputs)


def _viz_report(path: Path, chart: dict[str, Any]) -> Materialized:
    path.write_text(json.dumps({"chart": chart}, ensure_ascii=False), encoding="utf-8")
    return Materialized("sha256:" + "c" * 64, "qa.manifest", path, {})


def test_chart_to_documents_builds_report_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from production_hub import rasterize

    def fake_png(svg: Path, png: Path, **_: Any) -> tuple[int, int]:
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG chart")
        return 720, 420

    monkeypatch.setattr(rasterize, "svg_to_png", fake_png)
    ctx = _ctx(tmp_path, "chart.svg", b"<svg width='720' height='420'/>", "chart.svg",
               {"title": "รายงาน", "customer_name": "ฝ่ายขาย", "issue_date": "2026-09-30", "summary": "a|b"})
    report = _viz_report(ctx.work_dir / "inputs" / "report.json", {
        "categories": ["อาหาร", "ของใช้"], "values": ["2000.50", "220"], "aggregate_total": "2220.50",
        "x_axis_label": "หมวด", "y_axis_label": "ยอดขาย", "value_unit": "THB",
    })
    run = adapters.build_chart_to_documents(SendContext(ctx.work_dir, [*ctx.artifacts, report], ctx.params, ctx.send_id))
    request = _request(list(run.argv))
    assert request["table"] == {"headers": ["หมวด", "ยอดขาย (THB)"], "rows": [["อาหาร", "2000.50"], ["ของใช้", "220"]]}
    assert request["totals"] == {"subtotal": "2220.50", "tax_total": "0", "grand_total": "2220.50"}
    assert request["summary_lines"] == ["a", "b"]
    assert request["charts"][0]["content_hash"] == hashlib.sha256(b"\x89PNG chart").hexdigest()
    assert run.argv[run.argv.index("--chart-root") + 1] == str(ctx.work_dir / "charts")
    with pytest.raises(FileNotFoundError):
        run.manifest_path()


def test_chart_to_documents_needs_viz_report(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "chart.svg", b"<svg/>", "chart.svg", {"title": "t", "customer_name": "c"})
    with pytest.raises(SendError, match="report.json"):
        adapters.build_chart_to_documents(ctx)
    broken = _viz_report(ctx.work_dir / "inputs" / "report.json", {"categories": ["a"], "values": []})
    with pytest.raises(SendError, match="incomplete|line up"):
        adapters.build_chart_to_documents(SendContext(ctx.work_dir, [*ctx.artifacts, broken], ctx.params, ctx.send_id))


def test_svg_size_reads_width_height_or_viewbox() -> None:
    from production_hub.rasterize import DEFAULT_SIZE, svg_size

    assert svg_size('<svg xmlns="x" width="720" height="420">') == (720, 420)
    assert svg_size('<svg viewBox="0 0 1080 1350">') == (1080, 1350)
    assert svg_size("<svg>") == DEFAULT_SIZE


def test_takeoff_to_spreadsheet_pivots_one_unit_and_method_per_column(tmp_path: Path) -> None:
    item = {"ifc_class": "IfcWall", "qto_name": "Qto_WallBaseQuantities"}
    takeoff = {
        "takeoff_id": "t1",
        "aggregates": [
            {**item, "quantity_name": "Length", "unit": "m", "method": "gross", "value": "4.5"},
            {**item, "quantity_name": "NetArea", "unit": "m2", "method": "gross", "value": "17.5"},
            {**item, "qto_name": "Qto_NetQuantities", "quantity_name": "Length", "unit": "m", "method": "net", "value": "2.4"},
        ],
        "rows": [
            {**item, "global_id": "a", "quantity_name": "Length", "unit": "m", "method": "gross", "value": "2.5"},
            {**item, "global_id": "b", "quantity_name": "Length", "unit": "m", "method": "gross", "value": "2"},
        ],
    }
    ctx = _ctx(tmp_path, "takeoff.json", json.dumps(takeoff).encode(), "bim.takeoff", {"title": "BOQ"})
    request = _request(list(adapters.build_takeoff_to_spreadsheet(ctx).argv))
    aggregates, elements = request["tables"]
    keys = [column["key"] for column in aggregates["columns"]]
    assert keys == ["ifc_class", "Length_m_gross", "NetArea_m2_gross", "Length_m_net"]
    assert aggregates["rows"] == [{"ifc_class": "IfcWall", "Length_m_gross": "4.5", "NetArea_m2_gross": "17.5",
                                   "Length_m_net": "2.4"}]
    assert [row["global_id"] for row in elements["rows"]] == ["a", "b"]
    assert request["title"] == "BOQ"


def test_takeoff_to_spreadsheet_rejects_csv_and_duplicates(tmp_path: Path) -> None:
    csv_ctx = _ctx(tmp_path, "takeoff.csv", b"a,b", "bim.takeoff", {})
    with pytest.raises(SendError, match="takeoff.json"):
        adapters.build_takeoff_to_spreadsheet(csv_ctx)
    dup = {"aggregates": [{"ifc_class": "IfcWall", "quantity_name": "Length", "unit": "m", "method": "gross", "value": "1"}] * 2,
           "rows": []}
    ctx = _ctx(tmp_path / "d", "takeoff.json", json.dumps(dup).encode(), "bim.takeoff", {})
    with pytest.raises(SendError, match="two"):
        adapters.build_takeoff_to_spreadsheet(ctx)


def test_bim_takeoff_manifest_ingest(tmp_path: Path) -> None:
    job = tmp_path / "jobs" / "fp1"
    job.mkdir(parents=True)
    (job / "takeoff.json").write_bytes(b'{"rows": []}')
    (job / "takeoff.csv").write_bytes(b"global_id\n")
    manifest = job / "qa-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "bim-takeoff-qa.v1", "status": "complete", "takeoff_id": "t1",
        "report_schema_version": "bim-takeoff-report.v1", "source_sha256": "e" * 64,
        "artifacts": {name: {"sha256": hashlib.sha256((job / name).read_bytes()).hexdigest()}
                      for name in ("takeoff.json", "takeoff.csv")},
    }), encoding="utf-8")
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        result = ingest_manifest(hub, conn, manifest)
        record = hub.db.get(conn, result.outputs[1].artifact_id)
    assert [item.filename for item in result.outputs] == ["takeoff.csv", "takeoff.json"]
    assert record is not None and record["attributes"]["unregistered_inputs"] == ["sha256:" + "e" * 64]


def test_copy_to_web_builds_landing_request_with_brand_theme(tmp_path: Path) -> None:
    kit = {"colors": {"primary": "#0A7CFF", "text": "#222222", "background": "#FFFFFF"}}
    ctx = _ctx(tmp_path, "copy.txt", "พิมพ์สดทุกตัว".encode(), "copy.text",
               {"title": "เสื้อลายใหม่", "cta_label": "สั่งเลย", "slug": "New Print!", "features": "ส่งไว:3 วัน;สีไม่ตก:ซักได้"},
               brand=kit)
    run = adapters.build_copy_to_web(ctx)
    page = _request(list(run.argv))["page"]
    assert page["hero_body"] == "พิมพ์สดทุกตัว"
    assert page["slug"] == "new-print"
    assert page["features"] == [{"title": "ส่งไว", "body": "3 วัน"}, {"title": "สีไม่ตก", "body": "ซักได้"}]
    assert page["theme"] == {"primary": "#0A7CFF", "background": "#FFFFFF", "foreground": "#222222"}
    assert "web_production.cli" in run.argv


@pytest.mark.parametrize("features", ["no separator", ";", ":body", ";".join(f"t{i}:b" for i in range(7))])
def test_copy_to_web_rejects_bad_features(tmp_path: Path, features: str) -> None:
    ctx = _ctx(tmp_path, "copy.txt", b"body", "copy.text", {"title": "t", "cta_label": "c", "features": features})
    with pytest.raises(SendError, match="feature"):
        adapters.build_copy_to_web(ctx)


def test_web_manifest_ingest(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    files = {"p.html": b"<html></html>", "p.css": b"body{}"}
    for name, data in files.items():
        (out / name).write_bytes(data)
    manifest = out / "manifest-x.json"
    manifest.write_text(json.dumps({
        "schema_version": "web-manifest.v1", "status": "accepted", "page_id": "p", "idempotency_key": "k",
        "metadata": {"contract_version": "web.v1a", "recipe_version": "landing-template-v1", "template_ref": "landing-v1"},
        "artifacts": [{"filename": name, "media_type": "text/html" if name.endswith("html") else "text/css",
                       "content_hash": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
                      for name, data in files.items()],
    }), encoding="utf-8")
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        result = ingest_manifest(hub, conn, manifest)
    assert [item.kind for item in result.outputs] == ["web.page", "web.page"]


SRT = """1
00:00:00,000 --> 00:00:03,000
วันนี้มีเสื้อลายใหม่

2
00:00:03,000 --> 00:00:07,000
พิมพ์สดทุกตัว
ใส่สบาย

3
00:00:07,000 --> 00:00:12,000
ส่งไวภายในสามวัน
"""


def test_parse_srt_joins_multiline_cues() -> None:
    assert adapters.parse_srt(SRT) == ["วันนี้มีเสื้อลายใหม่", "พิมพ์สดทุกตัว ใส่สบาย", "ส่งไวภายในสามวัน"]


def test_captions_to_marketing_builds_campaign(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "clip.srt", SRT.encode(), "captions.srt",
               {"campaign_name": "ไลฟ์", "cues": "2-3", "channels": "facebook,email"})
    campaign = _request(list(adapters.build_captions_to_marketing(ctx).argv))
    assert campaign["channels"] == ["facebook", "email"]
    assert [asset["kind"] for asset in campaign["assets"]] == ["copy", "email"]
    assert campaign["assets"][0]["text"] == "พิมพ์สดทุกตัว ใส่สบาย ส่งไวภายในสามวัน"


@pytest.mark.parametrize("params", [{"cues": "0-2"}, {"cues": "2-9"}, {"cues": "a"}, {"channels": "tiktok"}])
def test_captions_to_marketing_rejects_bad_params(tmp_path: Path, params: dict[str, str]) -> None:
    ctx = _ctx(tmp_path, "clip.srt", SRT.encode(), "captions.srt", {"campaign_name": "x", **params})
    with pytest.raises(SendError):
        adapters.build_captions_to_marketing(ctx)


def test_graphic_to_video_builds_overlay_command(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "logo.png", b"\x89PNG", "graphic.png", {"position": "top-left", "width_ratio": "0.3"})
    with pytest.raises(SendError, match="exactly one video"):
        adapters.build_graphic_to_video(ctx)
    video = ctx.work_dir / "inputs" / "clip.mp4"
    video.write_bytes(b"mp4")
    run = adapters.build_graphic_to_video(SendContext(
        ctx.work_dir, [*ctx.artifacts, Materialized("sha256:" + "d" * 64, "video.render", video, {})], ctx.params, ctx.send_id,
    ))
    assert run.argv[3] == "overlay"
    assert run.argv[run.argv.index("--position") + 1] == "top-left"
    assert run.argv[run.argv.index("--width-ratio") + 1] == "0.3"
    assert run.manifest_path().name == "video-overlay.json"


def test_video_overlay_manifest_links_video_and_image(tmp_path: Path) -> None:
    sources = tmp_path / "src"
    sources.mkdir()
    (sources / "clip.mp4").write_bytes(b"video")
    (sources / "logo.png").write_bytes(b"logo")
    out = tmp_path / "out"
    out.mkdir()
    (out / "branded.mp4").write_bytes(b"branded")
    manifest = out / "video-overlay.json"
    manifest.write_text(json.dumps({
        "schema_version": "video-overlay.v1", "overlay_id": "branded", "status": "accepted", "recipe": "overlay-graphic.v1",
        "placement": {"position": "top-right"},
        "source_video": {"sha256": hashlib.sha256(b"video").hexdigest()},
        "source_image": {"sha256": hashlib.sha256(b"logo").hexdigest()},
        "output": {"filename": "branded.mp4", "media_type": "video/mp4", "sha256": hashlib.sha256(b"branded").hexdigest()},
        "qa": {"passed": True},
    }), encoding="utf-8")
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        result = ingest_manifest(hub, conn, manifest, input_files=[sources / "clip.mp4"])
        record = hub.db.get(conn, result.outputs[0].artifact_id)
    assert record is not None and record["kind"] == "video.render"
    assert record["lineage"] == [result.inputs[0].artifact_id]
    assert record["attributes"]["unregistered_inputs"] == ["sha256:" + hashlib.sha256(b"logo").hexdigest()]


def _cad_manifest(path: Path, **overrides: Any) -> Materialized:
    manifest = {
        "schema_version": "cad-qa-manifest.v1", "build_status": "accepted", "template": "mounting-plate.v1",
        "artifacts": {"part.FCStd": {}, "part.step": {}, "part.stl": {}},
        "qa": {"bounds_mm": [0.0, 137.0, 0.0, 83.0, 0.0, 7.5], "volume_mm3": 83774.5355, "bore_count": 4},
        **overrides,
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return Materialized("sha256:" + "e" * 64, "qa.manifest", path, {})


def test_cad_to_documents_builds_spec_sheet_with_preview(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "part.FCStd", b"fcstd", "cad.fcstd", {"title": "Spec", "customer_name": "ลูกค้า"})
    manifest = _cad_manifest(ctx.work_dir / "inputs" / "qa-manifest.json")
    preview_path = ctx.work_dir / "inputs" / "preview.png"
    preview_path.write_bytes(b"\x89PNG preview")
    preview = Materialized("sha256:" + "f" * 64, "image.render", preview_path, {})
    run = adapters.build_cad_to_documents(SendContext(ctx.work_dir, [*ctx.artifacts, manifest, preview], ctx.params, ctx.send_id))
    request = _request(list(run.argv))
    specs = {row["label"]: (row["value"], row["unit"]) for row in request["specs"]}
    assert specs["ความกว้าง (X)"] == ("137.00", "mm")
    assert specs["ความสูง (Z)"] == ("7.50", "mm")
    assert specs["ปริมาตร"] == ("83774.54", "mm3")
    assert specs["จำนวนรู"] == ("4", "")
    assert specs["ไฟล์ที่ส่งมอบ"][0] == "FCSTD, STEP, STL"
    assert request["notes"][-1] == adapters.CAD_DISCLAIMER_TH
    assert request["images"][0]["content_hash"] == hashlib.sha256(b"\x89PNG preview").hexdigest()
    assert "--image-root" in run.argv


def test_cad_to_documents_requires_accepted_manifest(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "part.FCStd", b"fcstd", "cad.fcstd", {"title": "Spec", "customer_name": "c"})
    with pytest.raises(SendError, match="qa-manifest"):
        adapters.build_cad_to_documents(ctx)
    rejected = _cad_manifest(ctx.work_dir / "inputs" / "qa-manifest.json", build_status="rejected")
    with pytest.raises(SendError, match="not an accepted"):
        adapters.build_cad_to_documents(SendContext(ctx.work_dir, [*ctx.artifacts, rejected], ctx.params, ctx.send_id))


def test_graphic_to_web_ships_hero_image_with_copy_body(tmp_path: Path) -> None:
    params = {"title": "t", "cta_label": "c", "features": "a:b", "alt": "ภาพประกาศ"}
    ctx = _ctx(tmp_path, "post.png", b"\x89PNG post", "graphic.png", params)
    with pytest.raises(SendError, match="hero text"):
        adapters.build_graphic_to_web(ctx)
    copy_path = ctx.work_dir / "inputs" / "copy.txt"
    copy_path.write_text("พิมพ์สดทุกตัว", encoding="utf-8")
    copy = Materialized("sha256:" + "a" * 64, "copy.text", copy_path, {})
    run = adapters.build_graphic_to_web(SendContext(ctx.work_dir, [*ctx.artifacts, copy], params, ctx.send_id))
    page = _request(list(run.argv))["page"]
    assert page["hero_body"] == "พิมพ์สดทุกตัว"
    assert page["hero_image"] == {"filename": "hero.png", "content_hash": hashlib.sha256(b"\x89PNG post").hexdigest(),
                                  "alt": "ภาพประกาศ"}
    assert Path(run.argv[run.argv.index("--asset-root") + 1], "hero.png").read_bytes() == b"\x89PNG post"


def test_cad_to_scene_converts_then_composes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> Any:
        calls.append(argv)
        Path(argv[argv.index("--") + 2]).write_bytes(b"glTF")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(adapters, "find_blender", lambda: "blender.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ctx = _ctx(tmp_path, "part.stl", b"solid", "cad.stl", {"color": "#336699", "render_profile": "landscape-1920x1080.v1"})
    run = adapters.build_cad_to_scene(ctx)
    assert calls[0][-1] == "#336699" and calls[0][-3].endswith("part.stl")
    scene = json.loads((ctx.work_dir / "scene.json").read_text(encoding="utf-8"))
    assert scene["asset"] == {"path": "part.glb"} and scene["render_profile"] == "landscape-1920x1080.v1"
    assert scene["composition"] == "three-quarter-fit.v1"
    assert run.argv[3:5] == ["scene", "compose"] and run.argv[-1] == "blender.exe"
    with pytest.raises(FileNotFoundError):
        run.manifest_path()


@pytest.mark.parametrize("params", [{"render_profile": "portrait"}, {"color": "blue"}, {"composition": "orbit"}])
def test_cad_to_scene_rejects_bad_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, params: dict[str, str]) -> None:
    monkeypatch.setattr(adapters, "find_blender", lambda: "blender.exe")
    with pytest.raises(SendError):
        adapters.build_cad_to_scene(_ctx(tmp_path, "part.stl", b"solid", "cad.stl", params))


def test_cad_to_scene_requires_blender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "find_blender", lambda: None)
    with pytest.raises(SendError, match="Blender"):
        adapters.build_cad_to_scene(_ctx(tmp_path, "part.stl", b"solid", "cad.stl", {}))


def test_audio_to_game_writes_game_ir_v2_with_pinned_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity = tmp_path / "Unity.exe"
    unity.write_bytes(b"fake")
    monkeypatch.setenv("PRODUCTION_HUB_UNITY", str(unity))
    monkeypatch.setenv("PRODUCTION_HUB_GAME_OUTPUT", str(tmp_path / "games"))
    ctx = _ctx(tmp_path, "master.wav", b"RIFF fake wav", "audio.master", {"scene_id": "Pod Launch Room!"})
    run = adapters.build_audio_to_game(ctx)
    game = json.loads(Path(run.argv[run.argv.index("--game") + 1]).read_text(encoding="utf-8"))
    assert game["schema_version"] == "game-ir.v2" and game["scene_id"] == "pod-launch-room"
    [asset] = game["assets"]
    assert asset == {"asset_id": "music", "kind": "audio", "filename": "music.wav",
                     "sha256": hashlib.sha256(b"RIFF fake wav").hexdigest()}
    assert game["audio"] == {"music": "music", "collect_sfx": None}
    asset_root = Path(run.argv[run.argv.index("--asset-root") + 1])
    assert (asset_root / "music.wav").read_bytes() == b"RIFF fake wav"
    assert run.argv[run.argv.index("--unity") + 1] == str(unity)
    assert run.argv[run.argv.index("--output-root") + 1] == str(tmp_path / "games")
    built = tmp_path / "games" / ("f" * 64)
    (ctx.work_dir / "tool-stdout.log").write_text(json.dumps({"artifact_dir": str(built)}) + "\n", encoding="utf-8")
    assert run.manifest_path() == built / "qa-manifest.json"


def test_audio_to_game_collect_role_and_bad_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity = tmp_path / "Unity.exe"
    unity.write_bytes(b"fake")
    monkeypatch.setenv("PRODUCTION_HUB_UNITY", str(unity))
    ctx = _ctx(tmp_path, "coin.wav", b"RIFF coin", "audio.master", {"scene_id": "room", "role": "collect_sfx"})
    game = json.loads(Path((run := adapters.build_audio_to_game(ctx)).argv[run.argv.index("--game") + 1])
                      .read_text(encoding="utf-8"))
    assert game["audio"] == {"music": None, "collect_sfx": "collect-sfx"}
    with pytest.raises(SendError, match="role"):
        adapters.build_audio_to_game(SendContext(ctx.work_dir, ctx.artifacts, {"scene_id": "r", "role": "x"}, ctx.send_id))
    with pytest.raises(SendError, match="scene_id"):
        adapters.build_audio_to_game(SendContext(ctx.work_dir, ctx.artifacts, {"scene_id": "!!"}, ctx.send_id))
