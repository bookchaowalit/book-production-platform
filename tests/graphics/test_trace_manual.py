"""Graphics job history, step trace, Figma manual guide, multi-size resize, and contrast QA."""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from book_production_platform.brief_adapter_kit.testing import ScriptedTransport
from book_production_platform.graphics_ir import TextNode
from book_production_platform.graphics_production import cli as graphics_cli
from book_production_platform.graphics_production import export as export_module
from book_production_platform.graphics_production import jobs
from book_production_platform.graphics_production.export import export_batch
from book_production_platform.graphics_production.jobs import expand_variants, run_batch
from book_production_platform.graphics_production.layout import wrap_text
from book_production_platform.graphics_production.manual import (
    build_figma_guide,
    line_height_px,
)
from book_production_platform.graphics_production.qa import (
    contrast_ratio,
    contrast_report,
)
from book_production_platform.graphics_production.render import RenderError, render_svg
from book_production_platform.graphics_production.templates import (
    VARIANT_SIZES,
    compile_template,
)
from book_production_platform.production_trace import (
    read_history,
    sha256_json,
    verify_trace,
)
from tests.graphics.test_brief_adapter import BRIEF, GOOD, adapter

CANARY = "CANARY-gfx-trace-ข้อความลับ"


def _item(request_id: str = "quote-001", quote: str = "ทำให้ชัดเจนและเรียบง่าย") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "template_id": "quote_card",
        "variant": "square",
        "canvas": {"width": 1080, "height": 1080},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"quote": quote, "author": "ทีมงาน"},
    }


def _announcement(variant: str = "portrait") -> dict[str, Any]:
    width, height = VARIANT_SIZES[variant]
    return {
        "request_id": f"ann-{variant}",
        "template_id": "announcement",
        "variant": variant,
        "canvas": {"width": width, "height": height},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {
            "headline": "เปิดรับสมัครสมาชิกใหม่ประจำเดือนตุลาคม",
            "body": "สมัครวันนี้รับส่วนลดพิเศษสำหรับคอร์สออนไลน์ทุกคอร์ส และสิทธิ์เข้าร่วมเวิร์กช็อปฟรี",
            "cta": "สมัครเลย",
        },
    }


def _png(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def stub_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "render_png", lambda ir, *, asset_root=None: _png(ir.canvas.width, ir.canvas.height))
    monkeypatch.setattr(jobs, "run_graphic_qa", lambda svg, png, ir: ())


def _trace(result: jobs.BatchResult, root: Path) -> dict[str, Any]:
    return json.loads((root / result.batch_id / "trace.json").read_text(encoding="utf-8"))


def test_accepted_batch_writes_trace_ir_manual_and_history(tmp_path: Path, stub_browser: None) -> None:
    result = run_batch([_item(), _announcement()], tmp_path, "trace-ok")

    assert result.status == "accepted"
    batch_dir = tmp_path / result.batch_id
    trace = _trace(result, tmp_path)
    assert trace["schema_version"] == "production-trace.v1"
    assert trace["tool_id"] == "graphics-v1a"
    assert trace["job_id"] == result.batch_id
    assert trace["status"] == "accepted"
    assert trace["ai"] == {"used": False, "model_calls": 0}
    operations = [step["operation"] for step in trace["steps"]]
    for operation in ("compile_template", "render_svg", "render_png", "graphic_qa", "write_artifacts", "write_manifest"):
        assert operation in operations
    assert {artifact["file"] for artifact in trace["artifacts"]} == {"quote-001.svg", "quote-001.png", "ann-portrait.svg", "ann-portrait.png"}
    ir_document = json.loads((batch_dir / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"] == {"schema": "graphics-batch-ir.v1", "sha256": sha256_json(ir_document), "file": "ir.json"}
    assert [item["request_id"] for item in ir_document["items"]] == ["quote-001", "ann-portrait"]
    assert trace["manual_guide"]["program"] == "Figma"
    assert (batch_dir / "MANUAL-STEPS.md").is_file()
    history = read_history(tmp_path)
    assert [(entry["job_id"], entry["status"]) for entry in history] == [(result.batch_id, "accepted")]
    assert history[0]["trace"] == f"{result.batch_id}/trace.json"


def test_verify_passes_then_fails_after_tamper(tmp_path: Path, stub_browser: None) -> None:
    result = run_batch([_item()], tmp_path, "trace-verify")
    assert verify_trace(tmp_path, result.batch_id)["ok"] is True

    (tmp_path / result.batch_id / "quote-001.png").write_bytes(b"tampered")

    report = verify_trace(tmp_path, result.batch_id)
    assert report["ok"] is False
    assert [item["file"] for item in report["artifacts"] if not item["ok"]] == ["quote-001.png"]


def test_trace_and_history_never_hold_content_text(tmp_path: Path, stub_browser: None) -> None:
    result = run_batch([_item(quote=f"{CANARY} คำคมสั้น")], tmp_path, "trace-canary")

    trace_text = (tmp_path / result.batch_id / "trace.json").read_text(encoding="utf-8")
    history_text = (tmp_path / "history.jsonl").read_text(encoding="utf-8")
    assert CANARY not in trace_text
    assert CANARY not in history_text
    assert "ทีมงาน" not in trace_text
    # The manual guide is the human copy of the content; it sits next to the artifact.
    assert "CANARY" in (tmp_path / result.batch_id / "MANUAL-STEPS.md").read_text(encoding="utf-8")


def test_rejected_batch_is_traced_as_failed_with_the_failing_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_browser(ir: object, *, asset_root: object = None) -> bytes:
        raise RenderError("browser_not_found")

    monkeypatch.setattr(jobs, "render_png", no_browser)
    result = run_batch([_item(quote=CANARY)], tmp_path, "trace-fail")

    assert result.status == "rejected"
    trace = _trace(result, tmp_path)
    assert trace["status"] == "failed"
    png_step = next(step for step in trace["steps"] if step["operation"] == "render_png")
    assert png_step["status"] == "failed" and png_step["note"] == "RenderError"
    assert any(step["operation"] == "rejected" and step["note"] == "browser_not_found" for step in trace["steps"])
    assert trace["artifacts"] == []
    assert read_history(tmp_path)[0]["status"] == "failed"
    assert CANARY not in json.dumps(trace, ensure_ascii=False)


def test_unexpected_error_is_traced_and_still_raised(tmp_path: Path, stub_browser: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def disk_full(path: Path, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(jobs, "_write_atomic", disk_full)
    with pytest.raises(OSError, match="disk full"):
        run_batch([_item()], tmp_path, "trace-oserror")

    history = read_history(tmp_path)
    assert len(history) == 1 and history[0]["status"] == "failed"
    trace = json.loads((tmp_path / history[0]["trace"]).read_text(encoding="utf-8"))
    assert trace["steps"][-1] == {**trace["steps"][-1], "operation": "failed", "note": "OSError"}


def test_ai_proposal_metadata_is_traced_without_text(tmp_path: Path, stub_browser: None) -> None:
    proposal = {"adapter": "provider-gateway", "adapter_version": "graphics-brief-adapter.v1", "mode": "live", "model_calls": 1, "templates": ["quote_card"], "brief": CANARY}

    result = run_batch([_item()], tmp_path, "trace-ai", ai_proposal=proposal)

    trace = _trace(result, tmp_path)
    assert trace["ai"]["used"] is True
    assert trace["ai"]["model_calls"] == 1
    assert trace["ai"]["adapter"] == "provider-gateway@graphics-brief-adapter.v1"
    assert trace["ai"]["provided"] == ["quote_card"]
    assert read_history(tmp_path)[0]["ai_used"] is True
    assert CANARY not in json.dumps(trace, ensure_ascii=False)


def test_cli_brief_records_proposal_metadata_and_batch_traces_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graphics_cli, "_provider_adapter", lambda path, root: adapter(tmp_path, ScriptedTransport(GOOD)))
    monkeypatch.setattr(jobs, "render_png", lambda ir, *, asset_root=None: _png(ir.canvas.width, ir.canvas.height))
    monkeypatch.setattr(jobs, "run_graphic_qa", lambda svg, png, ir: ())
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps({"brief": BRIEF, "locale": "en-US"}), encoding="utf-8")
    assert graphics_cli.main(["brief", "--input", str(brief_path), "--output", str(tmp_path / "batch.json"), "--job-id", "trace-cli"]) == 0
    batch = json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))
    assert batch["proposal"]["model_calls"] == 1
    assert "Done is better" not in json.dumps(batch["proposal"])

    assert graphics_cli.main(["batch", "--input", str(tmp_path / "batch.json"), "--output", str(tmp_path / "out"), "--idempotency-key", "trace-cli"]) == 0

    entry = read_history(tmp_path / "out")[0]
    assert entry["ai_used"] is True
    trace_text = (tmp_path / "out" / entry["trace"]).read_text(encoding="utf-8")
    assert "Done is better" not in trace_text and "CANARY-gfx-unit" not in trace_text


def test_figma_guide_values_equal_the_ir_and_the_rendered_svg() -> None:
    ir = compile_template(_announcement("story"))
    guide = build_figma_guide([("ann-story", ir)], batch_id="b1")
    steps = guide.steps
    values = [step.value for step in steps]

    assert guide.program == "Figma"
    assert guide.alternatives == ["Canva", "Adobe Photoshop"]
    assert "W=1080, H=1920" in values
    assert [layer.name for layer in guide.layers] == ["ann-story", *[node.node_id for node in ir.nodes]]
    svg = render_svg(ir)
    rendered_lines = [re.sub(r"<[^>]+>", "", line) for line in re.findall(r"<tspan[^>]*>(.*?)</tspan>", svg)]
    typed_lines: list[str] = []
    for node in ir.nodes:
        box = f"X={node.x:g}, Y={node.y:g}, W={node.width:g}, H={node.height:g}"
        assert box in values
        if isinstance(node, TextNode):
            assert f"{node.font_size:g}" in values
            assert f"{round(node.font_size * 1.2, 2):g}px" == f"{line_height_px(node):g}px"
            assert f"{line_height_px(node):g}px" in values
            assert node.color.lstrip("#") in values
            lines = wrap_text(node.text, node.width, node.font_size)
            typed = [step.value for step in steps if step.target == f"กล่อง `{node.node_id}`"]
            assert typed == [line or "(บรรทัดว่าง)" for line in lines]
            typed_lines.extend(lines)
        else:
            assert getattr(node, "fill", "#").lstrip("#") in values or node.node_type == "image"
    # Every typed line equals the engine's <tspan> text (Thai line breaks included).
    assert typed_lines == rendered_lines
    assert any(len(lines) > 1 for lines in [wrap_text(n.text, n.width, n.font_size) for n in ir.nodes if isinstance(n, TextNode)])
    assert "ann-story.png, ann-story.svg" in values
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0


def test_expand_variants_resizes_one_design_to_every_size(tmp_path: Path, stub_browser: None) -> None:
    base = {key: value for key, value in _item("promo").items() if key not in {"variant", "canvas"}}
    expanded = expand_variants([{**base, "variants": ["square", "portrait", "story"]}])

    assert [(item["request_id"], item["variant"], item["canvas"]["width"], item["canvas"]["height"]) for item in expanded] == [
        ("promo-square", "square", 1080, 1080),
        ("promo-portrait", "portrait", 1080, 1350),
        ("promo-story", "story", 1080, 1920),
    ]
    result = run_batch([{**base, "variants": ["square", "portrait", "story"]}], tmp_path, "resize")
    assert result.status == "accepted"
    assert [item.request_id for item in result.items] == ["promo-square", "promo-portrait", "promo-story"]
    assert {artifact.height for item in result.items for artifact in item.artifacts} == {1080, 1350, 1920}


def test_expand_variants_leaves_ambiguous_items_for_the_validator(tmp_path: Path, stub_browser: None) -> None:
    ambiguous = {**_item("both"), "variants": ["story"]}
    duplicated = {**{k: v for k, v in _item("dup").items() if k != "variant"}, "variants": ["square", "square"]}
    assert expand_variants([ambiguous, duplicated]) == [ambiguous, duplicated]

    result = run_batch([ambiguous, {**{k: v for k, v in _item("odd").items() if k != "variant"}, "variants": ["poster"]}], tmp_path, "resize-bad")
    assert result.status == "rejected"
    assert {item.error_code for item in result.items} == {"invalid_request"}


def test_contrast_report_follows_wcag() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == 1.0
    ir = compile_template(_item())
    report = {row["node_id"]: row for row in contrast_report(ir)}
    assert report["quote"]["backdrop_node"] == "background"
    assert report["quote"]["passes_aa"] is True
    # The accent author line (#FCA311 on white) is below AA: advisory only.
    assert report["author"]["ratio"] < 3.0 and report["author"]["passes_aa"] is False
    announcement = {row["node_id"]: row for row in contrast_report(compile_template(_announcement()))}
    assert announcement["cta"]["backdrop_node"] == "cta-bg"


def test_print_export_is_traced_with_a_manual_guide(tmp_path: Path, stub_browser: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_module, "_render_scaled", lambda path, width, height, scale: _png(width * scale, height * scale))
    batch = run_batch([_item()], tmp_path / "batch", "export-trace")
    assert batch.manifest_path is not None

    export_batch(batch.manifest_path, tmp_path / "out", scale=2, dpi=150)

    entry = read_history(tmp_path / "out")[0]
    assert entry["status"] == "accepted"
    assert sorted(entry["artifacts"]) == ["quote-001-print.pdf", "quote-001@2x.png"]
    assert verify_trace(tmp_path / "out", entry["job_id"])["ok"] is True
    manual = (tmp_path / "out" / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "Resolution = 150 Pixels/Inch" in manual
    assert "PNG, 2x" in manual
