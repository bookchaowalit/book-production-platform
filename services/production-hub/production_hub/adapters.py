"""Send adapters for concrete handoff edges.

Each adapter writes the target tool's own request into the send work folder
and returns the tool's CLI command. Tools run with the interpreter and
``PYTHONPATH`` their owners document; the Hub imports none of their code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT
from .send import OptionalParam, SendAdapter, SendContext, SendError, ToolRun, register_adapter

BPP_ROOT = REPO_ROOT
# Shared stdlib-only job history/trace package that every root engine imports.
PRODUCTION_TRACE = REPO_ROOT / "packages" / "production-trace"
GRAPHICS_VARIANTS = {"square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920)}
DEFAULT_BRAND = {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111", "background": "#FFFFFF"}


def _bpp_python() -> str:
    override = os.environ.get("PRODUCTION_HUB_BPP_PYTHON")
    if override:
        return override
    venv = BPP_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.is_file() else sys.executable


def _bpp_env() -> dict[str, str]:
    existing = os.environ.get("PYTHONPATH")
    paths = [str(BPP_ROOT / "src"), *([existing] if existing else [])]
    return {"PYTHONPATH": os.pathsep.join(paths), "PYTHONIOENCODING": "utf-8"}


def _brand(ctx: SendContext) -> dict[str, str]:
    brand = dict(DEFAULT_BRAND)
    if ctx.brand:
        colors = ctx.brand.get("colors", {})
        brand.update({
            "primary": colors.get("primary", brand["primary"]),
            "accent": colors.get("accent") or colors.get("secondary") or brand["accent"],
            "foreground": colors.get("text", brand["foreground"]),
            "background": colors.get("background", brand["background"]),
        })
    for key in ("primary", "accent", "foreground", "background"):
        if ctx.params.get(key):
            brand[key] = ctx.params[key]
    return brand


def _variants(params: Mapping[str, str]) -> list[str]:
    variants = [item.strip() for item in params.get("variants", "square").split(",") if item.strip()]
    unknown = sorted(set(variants) - set(GRAPHICS_VARIANTS))
    if unknown or not variants:
        raise SendError(f"variants must be a comma list of {', '.join(GRAPHICS_VARIANTS)}")
    return variants


def _graphics_run(ctx: SendContext, items: list[dict[str, Any]]) -> ToolRun:
    request_path = ctx.work_dir / "graphics-request.json"
    request_path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    key = f"hub-send-{ctx.send_id}"
    manifest = out / f"manifest-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}.json"
    return ToolRun(
        argv=[_bpp_python(), "-m", "book_production_platform.cli", "graphics", "batch",
              "--input", str(request_path), "--output", str(out), "--idempotency-key", key],
        cwd=ctx.work_dir, env=_bpp_env(), manifest_path=lambda: manifest,
    )


def build_chart_to_graphics(ctx: SendContext) -> ToolRun:
    """E4: place a chart SVG in the product_card image slot, one item per variant."""
    chart = ctx.artifacts[0]
    items = []
    for variant in _variants(ctx.params):
        width, height = GRAPHICS_VARIANTS[variant]
        items.append({
            "request_id": f"chart-{ctx.send_id[-8:]}-{variant}",
            "template_id": "product_card",
            "variant": variant,
            "canvas": {"width": width, "height": height},
            "brand": _brand(ctx),
            "content": {"title": ctx.params["title"], "price": ctx.params["caption"]},
            "assets": {"product_image": chart.path.name},
            "asset_root": str(chart.path.parent),
        })
    return _graphics_run(ctx, items)


register_adapter(SendAdapter(
    "E4-chart-to-graphics", build_chart_to_graphics, required_params=frozenset({"title", "caption"}),
    optional_params={
        "variants": OptionalParam(default="square", choices=tuple(GRAPHICS_VARIANTS),
                                  description="รูปทรงภาพที่ต้องการ คั่นด้วยคอมมาได้"),
    },
))


def _copy_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SendError("copy text is empty")
    return text


def build_copy_to_graphics(ctx: SendContext) -> ToolRun:
    """E6: approved channel copy becomes an announcement (or quote card) graphic."""
    body = _copy_text(ctx.artifacts[0].path)
    template = ctx.params.get("template", "announcement")
    items = []
    for variant in _variants(ctx.params):
        width, height = GRAPHICS_VARIANTS[variant]
        if template == "quote_card":
            content = {"quote": body, "author": ctx.params.get("author", ctx.params["headline"])}
        elif template == "announcement":
            content = {"headline": ctx.params["headline"], "body": body, "cta": ctx.params.get("cta", "ดูเพิ่มเติม")}
        else:
            raise SendError("template must be announcement or quote_card")
        items.append({
            "request_id": f"copy-{ctx.send_id[-8:]}-{variant}", "template_id": template, "variant": variant,
            "canvas": {"width": width, "height": height}, "brand": _brand(ctx), "content": content,
        })
    return _graphics_run(ctx, items)


register_adapter(SendAdapter(
    "E6-copy-to-graphics", build_copy_to_graphics, required_params=frozenset({"headline"}),
    optional_params={
        "template": OptionalParam(default="announcement", choices=("announcement", "quote_card"),
                                  description="แม่แบบกราฟิก"),
        "variants": OptionalParam(default="square", choices=tuple(GRAPHICS_VARIANTS),
                                  description="รูปทรงภาพที่ต้องการ คั่นด้วยคอมมาได้"),
        "author": OptionalParam(description="ชื่อผู้พูดสำหรับ quote_card (ค่าเริ่มต้น: headline)"),
        "cta": OptionalParam(default="ดูเพิ่มเติม", description="ข้อความปุ่ม CTA สำหรับ announcement"),
    },
))


def build_master_to_video(ctx: SendContext) -> ToolRun:
    """E1: replace a video's audio with the mastered track via video_postpipeline replace-audio."""
    master = ctx.artifacts[0]
    videos = [item for item in ctx.artifacts[1:] if item.kind in ("video.source", "video.render")]
    if len(videos) != 1:
        raise SendError("E1 needs exactly one video: add --with <video artifact id>")
    out = ctx.work_dir / "out"
    service = REPO_ROOT / "services" / "video-postproduction"
    existing = os.environ.get("PYTHONPATH")
    return ToolRun(
        argv=[sys.executable, "-m", "video_postpipeline.cli", "replace-audio",
              "--video", str(videos[0].path), "--audio", str(master.path), "--output-dir", str(out),
              "--remux-id", ctx.params.get("name", f"remaster-{ctx.send_id[-8:]}")],
        cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(service), *([existing] if existing else [])])},
        manifest_path=lambda: out / "video-remux.json",
    )


register_adapter(SendAdapter(
    "E1-master-to-video", build_master_to_video, extra_kinds=frozenset({"video.source", "video.render"}),
    optional_params={
        "name": OptionalParam(description="ชื่อผลงาน remux (ค่าเริ่มต้น: remaster-<send_id>)"),
    },
))


DOCUMENTS_SERVICE = REPO_ROOT / "services" / "document-production-api"
DOCUMENTS_IR = REPO_ROOT / "packages" / "document-production-ir"


def _chart_table(report: Mapping[str, Any]) -> tuple[list[str], list[list[str]], str]:
    chart = report.get("chart")
    if not isinstance(chart, dict):
        raise SendError("the data-viz report has no chart block")
    categories, values, total = chart.get("categories"), chart.get("values"), chart.get("aggregate_total")
    if not isinstance(categories, list) or not isinstance(values, list) or not isinstance(total, str):
        raise SendError("the data-viz chart block is incomplete")
    if not categories or len(categories) != len(values):
        raise SendError("the data-viz chart categories and values do not line up")
    value_header = str(chart.get("y_axis_label") or "Value")
    if chart.get("value_unit"):
        value_header = f"{value_header} ({chart['value_unit']})"
    headers = [str(chart.get("x_axis_label") or "Category"), value_header]
    return headers, [[str(c), str(v)] for c, v in zip(categories, values)], total


def build_chart_to_documents(ctx: SendContext) -> ToolRun:
    """E5: rasterize the chart and embed it in a report@1 document with the chart's own table."""
    from .rasterize import RasterizeError, svg_to_png

    chart = ctx.artifacts[0]
    reports = [item for item in ctx.artifacts[1:] if item.kind == "qa.manifest"]
    if len(reports) != 1:
        raise SendError("E5 needs the data-viz report.json: add --with <report.json artifact id>")
    try:
        report = json.loads(reports[0].path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SendError(f"cannot read the data-viz report: {exc}") from exc
    headers, rows, total = _chart_table(report)

    charts_dir = ctx.work_dir / "charts"
    png = charts_dir / "chart.png"
    try:
        svg_to_png(chart.path, png)
    except RasterizeError as exc:
        raise SendError(str(exc)) from exc
    data = png.read_bytes()
    request = {
        "schema_version": "report-document-request.v1",
        "document_id": ctx.params.get("document_id", f"report-{ctx.send_id[-8:]}"),
        "title": ctx.params["title"],
        "locale": ctx.params.get("locale", "th-TH"),
        "currency": ctx.params.get("currency", "THB"),
        "customer_name": ctx.params["customer_name"],
        "issue_date": ctx.params.get("issue_date", datetime.now().astimezone().date().isoformat()),
        "summary_lines": [line for line in ctx.params.get("summary", "").split("|") if line.strip()],
        "table": {"headers": headers, "rows": rows},
        "totals": {"subtotal": total, "tax_total": "0", "grand_total": total},
        "charts": [{"name": png.name, "media_type": "image/png",
                    "content_hash": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}],
    }
    request_path = ctx.work_dir / "report-request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    existing = os.environ.get("PYTHONPATH")

    def manifest() -> Path:
        found = sorted(out.glob("jobs/*/manifest.json"))
        if len(found) != 1:
            raise FileNotFoundError(f"expected one document manifest, found {len(found)}")
        return found[0]

    return ToolRun(
        argv=[sys.executable, "-m", "document_production.cli", "report", "--input", str(request_path),
              "--output", str(out), "--idempotency-key", f"hub-send-{ctx.send_id}", "--chart-root", str(charts_dir)],
        cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(DOCUMENTS_SERVICE), str(DOCUMENTS_IR), *([existing] if existing else [])]),
             "PYTHONIOENCODING": "utf-8"},
        manifest_path=manifest,
    )


register_adapter(SendAdapter(
    "E5-chart-to-documents", build_chart_to_documents, extra_kinds=frozenset({"qa.manifest"}),
    required_params=frozenset({"title", "customer_name"}),
    optional_params={
        "document_id": OptionalParam(description="รหัสเอกสาร (ค่าเริ่มต้น: report-<send_id>)"),
        "locale": OptionalParam(default="th-TH", description="ภาษาเอกสาร"),
        "currency": OptionalParam(default="THB", description="สกุลเงินในรายงาน"),
        "issue_date": OptionalParam(description="วันที่ออกเอกสาร ค.ศ. (ค่าเริ่มต้น: วันนี้)"),
        "summary": OptionalParam(description="บทสรุปหลายบรรทัด คั่นแต่ละบรรทัดด้วย |"),
    },
))


def _spreadsheet_run(ctx: SendContext, request: dict[str, Any]) -> ToolRun:
    request_path = ctx.work_dir / "workbook-request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    key = f"hub-send-{ctx.send_id}"
    manifest = out / f"job-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}" / "manifest.json"
    return ToolRun(
        argv=[_bpp_python(), "-m", "book_production_platform.cli", "spreadsheet", "build",
              "--input", str(request_path), "--output", str(out), "--idempotency-key", key],
        cwd=ctx.work_dir, env=_bpp_env(), manifest_path=lambda: manifest,
    )


def _quantity_key(item: Mapping[str, Any]) -> str:
    raw = f"{item.get('quantity_name', 'value')}_{item.get('unit', '')}_{item.get('method', '')}".strip("_")
    key = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_") or "value"
    return key if re.match(r"^[A-Za-z_]", key) else f"q_{key}"


def _pivot(items: list[Mapping[str, Any]], group_keys: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One row per group, one decimal column per quantity+unit+method, so a column never mixes units or methods."""
    quantity_columns: dict[str, str] = {}
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in items:
        key = _quantity_key(item)
        quantity_columns.setdefault(
            key, f"{item.get('quantity_name', 'Value')} ({item.get('unit', '')}, {item.get('method', '')})"
        )
        group = tuple(str(item.get(name, "")) for name in group_keys)
        row = grouped.setdefault(group, dict(zip(group_keys, group)))
        if key in row:
            raise SendError(f"takeoff has two {key} values for {group}")
        row[key] = str(item.get("value", ""))
    labels = {"global_id": "IFC GlobalId", "ifc_class": "IFC class"}
    columns = [{"key": name, "label": labels[name], "data_type": "text", "required": True} for name in group_keys]
    columns += [{"key": key, "label": label, "data_type": "decimal"} for key, label in quantity_columns.items()]
    return columns, list(grouped.values())


def build_takeoff_to_spreadsheet(ctx: SendContext) -> ToolRun:
    """E11: BIM takeoff JSON -> workbook with pivoted aggregate and per-element sheets (one unit per column)."""
    takeoff_path = ctx.artifacts[0].path
    if takeoff_path.suffix.lower() != ".json":
        raise SendError("E11 needs the takeoff.json artifact (not the CSV)")
    try:
        takeoff = json.loads(takeoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SendError(f"cannot read takeoff: {exc}") from exc
    aggregates, rows = takeoff.get("aggregates"), takeoff.get("rows")
    if not isinstance(aggregates, list) or not isinstance(rows, list) or not aggregates:
        raise SendError("takeoff has no aggregates or rows")
    aggregate_columns, aggregate_rows = _pivot(aggregates, ["ifc_class"])
    element_columns, element_rows = _pivot(rows, ["global_id", "ifc_class"])
    if len(element_rows) > 1000:
        raise SendError("takeoff has more than 1000 elements; split it before building a workbook")
    source = f"bim:{takeoff.get('takeoff_id', 'takeoff')}"
    request = {
        "workbook_id": ctx.params.get("workbook_id", f"boq-{ctx.send_id[-8:]}"),
        "title": ctx.params.get("title", f"Quantity takeoff {takeoff.get('takeoff_id', '')}".strip()),
        "locale": ctx.params.get("locale", "th-TH"),
        "tables": [
            {"name": "Aggregates", "source_ref": f"{source}:aggregates", "columns": aggregate_columns,
             "rows": aggregate_rows},
            {"name": "Elements", "source_ref": f"{source}:rows", "columns": element_columns, "rows": element_rows},
        ],
    }
    return _spreadsheet_run(ctx, request)


register_adapter(SendAdapter(
    "E11-takeoff-to-spreadsheet", build_takeoff_to_spreadsheet,
    optional_params={
        "workbook_id": OptionalParam(description="รหัสสมุดงาน (ค่าเริ่มต้น: boq-<send_id>)"),
        "title": OptionalParam(description="ชื่อสมุดงาน (ค่าเริ่มต้น: ชื่อ takeoff)"),
        "locale": OptionalParam(default="th-TH", description="ภาษาสมุดงาน"),
    },
))


WEB_SERVICE = REPO_ROOT / "services" / "web-production-api"
WEB_IR = REPO_ROOT / "packages" / "web-production-ir"


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or fallback


def _features(raw: str) -> list[dict[str, str]]:
    """Parse "title:body;title:body" (1-6 items) into landing-v1 features."""
    features = []
    for chunk in (item.strip() for item in raw.split(";")):
        if not chunk:
            continue
        title, separator, body = chunk.partition(":")
        if not separator or not title.strip() or not body.strip():
            raise SendError("features must look like 'title:body;title:body'")
        features.append({"title": title.strip(), "body": body.strip()})
    if not 1 <= len(features) <= 6:
        raise SendError("a landing page needs between 1 and 6 features")
    return features


def _web_run(ctx: SendContext, body: str, hero_image: dict[str, str] | None, asset_root: Path | None) -> ToolRun:
    if len(body) > 2000:
        raise SendError("copy is longer than the 2,000 character hero body limit")
    page_id = ctx.params.get("page_id", f"page-{ctx.send_id[-8:]}")
    theme = {}
    if ctx.brand:
        colors = ctx.brand.get("colors", {})
        theme = {key: colors[source] for key, source in
                 (("primary", "primary"), ("background", "background"), ("foreground", "text")) if colors.get(source)}
    page: dict[str, Any] = {
        "page_id": page_id,
        "language": ctx.params.get("language", "th"),
        "slug": _slug(ctx.params.get("slug", page_id), page_id),
        "title": ctx.params["title"],
        "description": ctx.params.get("description", body[:500]),
        "hero_heading": ctx.params.get("hero_heading", ctx.params["title"]),
        "hero_body": body,
        "hero_cta": {"label": ctx.params["cta_label"], "href": ctx.params.get("cta_href", "#contact")},
        "features": _features(ctx.params["features"]),
        "footer_text": ctx.params.get("footer", ""),
    }
    if theme:
        page["theme"] = theme
    if hero_image:
        page["hero_image"] = hero_image
    request_path = ctx.work_dir / "page-request.json"
    request_path.write_text(json.dumps({"page": page}, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    key = f"hub-send-{ctx.send_id}"
    manifest = out / f"manifest-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}.json"
    existing = os.environ.get("PYTHONPATH")
    argv = [sys.executable, "-m", "web_production.cli", "build", "--input", str(request_path),
            "--output", str(out), "--idempotency-key", key]
    if asset_root is not None:
        argv += ["--asset-root", str(asset_root)]
    return ToolRun(
        argv=argv, cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(WEB_SERVICE), str(WEB_IR), *([existing] if existing else [])]),
             "PYTHONIOENCODING": "utf-8"},
        manifest_path=lambda: manifest,
    )


def build_copy_to_web(ctx: SendContext) -> ToolRun:
    """E7a: approved copy becomes the hero body of a landing-v1 page."""
    return _web_run(ctx, _copy_text(ctx.artifacts[0].path), None, None)


def build_graphic_to_web(ctx: SendContext) -> ToolRun:
    """E7b: a graphic becomes the hero image of a landing page; copy comes from --with copy.text or --param body."""
    graphic = ctx.artifacts[0]
    copies = [item for item in ctx.artifacts[1:] if item.kind == "copy.text"]
    if len(copies) > 1:
        raise SendError("E7b accepts at most one copy.text with --with")
    body = _copy_text(copies[0].path) if copies else ctx.params.get("body", "").strip()
    if not body:
        raise SendError("E7b needs the hero text: add --with <copy.text> or --param body=...")
    suffix = graphic.path.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise SendError("the hero image must be PNG, JPEG, or WebP")
    assets = ctx.work_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    data = graphic.path.read_bytes()
    filename = f"hero{suffix}"
    (assets / filename).write_bytes(data)
    hero = {"filename": filename, "content_hash": hashlib.sha256(data).hexdigest(), "alt": ctx.params["alt"]}
    return _web_run(ctx, body, hero, assets)


WEB_OPTIONAL_PARAMS = {
    "page_id": OptionalParam(description="รหัสหน้าเว็บ (ค่าเริ่มต้น: page-<send_id>)"),
    "language": OptionalParam(default="th", description="ภาษาของหน้าเว็บ"),
    "slug": OptionalParam(description="slug ของ URL (ค่าเริ่มต้น: จาก page_id)"),
    "description": OptionalParam(description="คำอธิบายหน้าเว็บ (ค่าเริ่มต้น: ตัดจาก hero body)"),
    "hero_heading": OptionalParam(description="หัวเรื่อง hero (ค่าเริ่มต้น: title)"),
    "cta_href": OptionalParam(default="#contact", description="ลิงก์ปุ่ม CTA"),
    "footer": OptionalParam(description="ข้อความท้ายหน้า"),
}

register_adapter(SendAdapter(
    "E7a-copy-to-web", build_copy_to_web, required_params=frozenset({"title", "cta_label", "features"}),
    optional_params=WEB_OPTIONAL_PARAMS,
))
register_adapter(SendAdapter(
    "E7b-graphic-to-web", build_graphic_to_web, extra_kinds=frozenset({"copy.text"}),
    required_params=frozenset({"title", "cta_label", "features", "alt"}),
    optional_params={
        "body": OptionalParam(description="ข้อความ hero (ใช้เมื่อไม่มี --with copy.text)"),
        **WEB_OPTIONAL_PARAMS,
    },
))


MARKETING_SERVICE = REPO_ROOT / "services" / "marketing-production-api"
MARKETING_IR = REPO_ROOT / "packages" / "marketing-production-ir"
MARKETING_CHANNELS = ("facebook", "instagram", "linkedin", "email", "line", "web")
_SRT_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")


def parse_srt(text: str) -> list[str]:
    """Return the text of each SRT cue in order (index and timing lines dropped)."""
    cues: list[str] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].isdigit():
            lines = lines[1:]
        if lines and _SRT_TIME.match(lines[0]):
            lines = lines[1:]
        if lines:
            cues.append(" ".join(lines))
    return cues


def _cue_range(raw: str, count: int) -> tuple[int, int]:
    first, _, last = raw.partition("-")
    try:
        start, end = int(first), int(last or first)
    except ValueError as exc:
        raise SendError("cues must look like '1-3' (1-based, inclusive)") from exc
    if not 1 <= start <= end <= count:
        raise SendError(f"cues must be within 1-{count}")
    return start, end


def build_captions_to_marketing(ctx: SendContext) -> ToolRun:
    """E2: an excerpt of caption cues becomes the per-channel copy of a review-only campaign."""
    cues = parse_srt(ctx.artifacts[0].path.read_text(encoding="utf-8-sig"))
    if not cues:
        raise SendError("the captions file has no cues")
    start, end = _cue_range(ctx.params.get("cues", f"1-{min(3, len(cues))}"), len(cues))
    excerpt = " ".join(cues[start - 1:end])
    channels = [item.strip() for item in ctx.params.get("channels", "facebook,instagram").split(",") if item.strip()]
    unknown = sorted(set(channels) - set(MARKETING_CHANNELS))
    if unknown or not channels:
        raise SendError(f"channels must be a comma list of {', '.join(MARKETING_CHANNELS)}")
    campaign_id = ctx.params.get("campaign_id", f"clip-{ctx.send_id[-8:]}")
    campaign = {
        "campaign_id": campaign_id,
        "name": ctx.params["campaign_name"],
        "objective": ctx.params.get("objective", "awareness"),
        "channels": channels,
        "audience": {"name": ctx.params.get("audience", "ผู้ติดตาม"),
                     "rules": [{"field": "language", "operator": "equals", "value": ctx.params.get("language", "th")}]},
        "assets": [
            {"asset_id": f"clip-{channel}", "kind": "email" if channel == "email" else "copy", "channel": channel,
             "uri": f"inline:clip-{channel}", "text": excerpt}
            for channel in channels
        ],
    }
    request_path = ctx.work_dir / "campaign-request.json"
    request_path.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    key = f"hub-send-{ctx.send_id}"
    manifest = out / f"manifest-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}.json"
    existing = os.environ.get("PYTHONPATH")
    return ToolRun(
        argv=[sys.executable, "-m", "marketing_production.cli", "build", "--input", str(request_path),
              "--output", str(out), "--idempotency-key", key],
        cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(MARKETING_SERVICE), str(MARKETING_IR), *([existing] if existing else [])]),
             "PYTHONIOENCODING": "utf-8"},
        manifest_path=lambda: manifest,
    )


register_adapter(SendAdapter(
    "E2-captions-to-marketing", build_captions_to_marketing, required_params=frozenset({"campaign_name"}),
    optional_params={
        "cues": OptionalParam(default="1-3", description="ช่วง cue ที่ใช้ เช่น 1-3"),
        "channels": OptionalParam(default="facebook,instagram", choices=MARKETING_CHANNELS,
                                  description="ช่องทางที่จะโพสต์ คั่นด้วยคอมมาได้"),
        "campaign_id": OptionalParam(description="รหัสแคมเปญ (ค่าเริ่มต้น: clip-<send_id>)"),
        "objective": OptionalParam(default="awareness", description="เป้าหมายแคมเปญ"),
        "audience": OptionalParam(default="ผู้ติดตาม", description="ชื่อกลุ่มเป้าหมาย"),
        "language": OptionalParam(default="th", description="ภาษาของกลุ่มเป้าหมาย"),
    },
))


def build_graphic_to_video(ctx: SendContext) -> ToolRun:
    """E3: burn a graphic onto a video with video_postpipeline overlay."""
    graphic = ctx.artifacts[0]
    videos = [item for item in ctx.artifacts[1:] if item.kind in ("video.source", "video.render")]
    if len(videos) != 1:
        raise SendError("E3 needs exactly one video: add --with <video artifact id>")
    out = ctx.work_dir / "out"
    service = REPO_ROOT / "services" / "video-postproduction"
    existing = os.environ.get("PYTHONPATH")
    return ToolRun(
        argv=[sys.executable, "-m", "video_postpipeline.cli", "overlay", "--video", str(videos[0].path),
              "--image", str(graphic.path), "--output-dir", str(out),
              "--position", ctx.params.get("position", "bottom-right"),
              "--width-ratio", ctx.params.get("width_ratio", "0.2"), "--margin", ctx.params.get("margin", "24"),
              "--overlay-id", ctx.params.get("name", f"overlay-{ctx.send_id[-8:]}")],
        cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(service), *([existing] if existing else [])])},
        manifest_path=lambda: out / "video-overlay.json",
    )


register_adapter(SendAdapter(
    "E3-graphic-to-video", build_graphic_to_video, extra_kinds=frozenset({"video.source", "video.render"}),
    optional_params={
        "position": OptionalParam(default="bottom-right", description="ตำแหน่งภาพซ้อนบนวิดีโอ"),
        "width_ratio": OptionalParam(default="0.2", description="ความกว้างภาพเทียบวิดีโอ (0-1)"),
        "margin": OptionalParam(default="24", description="ระยะขอบเป็นพิกเซล"),
        "name": OptionalParam(description="ชื่อผลงาน overlay (ค่าเริ่มต้น: overlay-<send_id>)"),
    },
))


CAD_DISCLAIMER_TH = "ข้อมูลจากแบบจำลอง CAD V1A ใช้เพื่ออ้างอิงเท่านั้น ไม่ใช่การรับรองทางวิศวกรรม"


def _cad_specs(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    qa = manifest.get("qa", {})
    specs: list[dict[str, str]] = []
    bounds = qa.get("bounds_mm")
    if isinstance(bounds, list) and len(bounds) == 6:
        for label, low, high in (("ความกว้าง (X)", 0, 1), ("ความลึก (Y)", 2, 3), ("ความสูง (Z)", 4, 5)):
            specs.append({"label": label, "value": f"{float(bounds[high]) - float(bounds[low]):.2f}", "unit": "mm"})
    if isinstance(qa.get("volume_mm3"), (int, float)):
        specs.append({"label": "ปริมาตร", "value": f"{qa['volume_mm3']:.2f}", "unit": "mm3"})
    for key, label in (("bore_count", "จำนวนรู"), ("solid_count", "จำนวน solid"), ("stl_triangle_count", "สามเหลี่ยม STL")):
        if isinstance(qa.get(key), int):
            specs.append({"label": label, "value": str(qa[key]), "unit": ""})
    if manifest.get("template"):
        specs.append({"label": "แม่แบบชิ้นงาน", "value": str(manifest["template"]), "unit": ""})
    formats = sorted(Path(name).suffix.lstrip(".").upper() for name in manifest.get("artifacts", {}))
    if formats:
        specs.append({"label": "ไฟล์ที่ส่งมอบ", "value": ", ".join(formats), "unit": ""})
    return specs


def build_cad_to_documents(ctx: SendContext) -> ToolRun:
    """E10: a CAD part becomes a spec-sheet@1 document with its measured dimensions and preview."""
    manifests = [item for item in ctx.artifacts[1:] if item.kind == "qa.manifest"]
    previews = [item for item in ctx.artifacts[1:] if item.kind == "image.render"]
    if len(manifests) != 1 or len(previews) > 1:
        raise SendError("E10 needs the CAD qa-manifest.json (--with) and optionally one preview render (--with)")
    try:
        manifest = json.loads(manifests[0].path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SendError(f"cannot read the CAD manifest: {exc}") from exc
    if manifest.get("schema_version") != "cad-qa-manifest.v1" or manifest.get("build_status") != "accepted":
        raise SendError("the --with manifest is not an accepted cad-qa-manifest.v1")
    specs = _cad_specs(manifest)
    if not specs:
        raise SendError("the CAD manifest has no measurable QA values")
    images_dir = ctx.work_dir / "images"
    images: list[dict[str, Any]] = []
    if previews:
        images_dir.mkdir(parents=True, exist_ok=True)
        data = previews[0].path.read_bytes()
        (images_dir / "preview.png").write_bytes(data)
        images.append({"name": "preview.png", "media_type": "image/png",
                       "content_hash": hashlib.sha256(data).hexdigest(), "byte_size": len(data)})
    notes = [line for line in ctx.params.get("notes", "").split("|") if line.strip()]
    request = {
        "schema_version": "spec-sheet-request.v1",
        "document_id": ctx.params.get("document_id", f"spec-{ctx.send_id[-8:]}"),
        "title": ctx.params["title"],
        "locale": ctx.params.get("locale", "th-TH"),
        "customer_name": ctx.params["customer_name"],
        "issue_date": ctx.params.get("issue_date", datetime.now().astimezone().date().isoformat()),
        "subject": ctx.params.get("subject", ctx.params["title"]),
        "specs": specs,
        "images": images,
        "notes": [*notes, CAD_DISCLAIMER_TH],
    }
    request_path = ctx.work_dir / "spec-sheet-request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"
    existing = os.environ.get("PYTHONPATH")

    def manifest_path() -> Path:
        found = sorted(out.glob("jobs/*/manifest.json"))
        if len(found) != 1:
            raise FileNotFoundError(f"expected one document manifest, found {len(found)}")
        return found[0]

    argv = [sys.executable, "-m", "document_production.cli", "spec-sheet", "--input", str(request_path),
            "--output", str(out), "--idempotency-key", f"hub-send-{ctx.send_id}"]
    if images:
        argv += ["--image-root", str(images_dir)]
    return ToolRun(
        argv=argv, cwd=ctx.work_dir,
        env={"PYTHONPATH": os.pathsep.join([str(PRODUCTION_TRACE), str(DOCUMENTS_SERVICE), str(DOCUMENTS_IR), *([existing] if existing else [])]),
             "PYTHONIOENCODING": "utf-8"},
        manifest_path=manifest_path,
    )


register_adapter(SendAdapter(
    "E10-cad-to-documents", build_cad_to_documents, extra_kinds=frozenset({"qa.manifest", "image.render"}),
    required_params=frozenset({"title", "customer_name"}),
    optional_params={
        "document_id": OptionalParam(description="รหัสเอกสาร (ค่าเริ่มต้น: spec-<send_id>)"),
        "locale": OptionalParam(default="th-TH", description="ภาษาเอกสาร"),
        "issue_date": OptionalParam(description="วันที่ออกเอกสาร ค.ศ. (ค่าเริ่มต้น: วันนี้)"),
        "subject": OptionalParam(description="หัวข้อเอกสาร (ค่าเริ่มต้น: title)"),
        "notes": OptionalParam(description="หมายเหตุเพิ่มเติม คั่นแต่ละบรรทัดด้วย |"),
    },
))


STL_TO_GLB_SCRIPT = Path(__file__).with_name("blender_stl_to_glb.py")
SCENE_RENDER_PROFILES = ("square-1024.v1", "landscape-1920x1080.v1")
SCENE_COMPOSITIONS = ("three-quarter-fit.v1", "front-fit.v1", "side-fit.v1", "top-fit.v1", "three-quarter.v1")


def find_blender() -> str | None:
    for variable in ("PRODUCTION_HUB_BLENDER", "BLENDER_BIN"):
        configured = os.environ.get(variable)
        if configured and Path(configured).is_file():
            return configured
    discovered = shutil.which("blender")
    if discovered:
        return discovered
    portable = (Path.home() / ".codex" / "tools" / "blender-5.2.2" / "portable" / "blender-5.2.2-windows-x64"
                / "blender.exe")
    return str(portable) if portable.is_file() else None


def build_cad_to_scene(ctx: SendContext) -> ToolRun:
    """E9: convert the CAD STL (mm) to a GLB (m) with Blender, then compose a product hero scene."""
    blender = find_blender()
    if blender is None:
        raise SendError("Blender was not found; set PRODUCTION_HUB_BLENDER")
    profile = ctx.params.get("render_profile", "square-1024.v1")
    if profile not in SCENE_RENDER_PROFILES:
        raise SendError(f"render_profile must be one of {', '.join(SCENE_RENDER_PROFILES)}")
    composition = ctx.params.get("composition", SCENE_COMPOSITIONS[0])
    if composition not in SCENE_COMPOSITIONS:
        raise SendError(f"composition must be one of {', '.join(SCENE_COMPOSITIONS)}")
    color = ctx.params.get("color", "#B8BEC6")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise SendError("color must be a #RRGGBB hex value")
    input_root = ctx.work_dir / "scene-input"
    input_root.mkdir(parents=True, exist_ok=True)
    glb = input_root / "part.glb"
    try:
        converted = subprocess.run(
            [blender, "--background", "--factory-startup", "--python", str(STL_TO_GLB_SCRIPT), "--",
             str(ctx.artifacts[0].path), str(glb), color],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SendError(f"STL to GLB conversion failed: {exc}") from exc
    (ctx.work_dir / "stl-to-glb.log").write_text((converted.stdout or "") + (converted.stderr or ""), encoding="utf-8")
    if converted.returncode != 0 or not glb.is_file():
        raise SendError("STL to GLB conversion failed; see stl-to-glb.log")
    scene = {
        "schema_version": "scene-ir.v1", "asset": {"path": glb.name}, "recipe": "product-hero.studio.v1",
        "composition": composition, "lighting": "softbox-three-point.v1", "background": "studio-neutral.v1",
        "material_preset": "preserve-source.v1", "render_profile": profile,
    }
    scene_path = ctx.work_dir / "scene.json"
    scene_path.write_text(json.dumps(scene, indent=2), encoding="utf-8")
    out = ctx.work_dir / "out"

    def manifest() -> Path:
        found = sorted(path for path in out.glob("*/qa-manifest.json") if not path.parent.name.startswith("."))
        if len(found) != 1:
            raise FileNotFoundError(f"expected one scene manifest, found {len(found)}")
        return found[0]

    return ToolRun(
        argv=[_bpp_python(), "-m", "book_production_platform.cli", "scene", "compose", "--scene", str(scene_path),
              "--input-root", str(input_root), "--output-root", str(out), "--blender", blender],
        cwd=ctx.work_dir, env=_bpp_env(), manifest_path=manifest, timeout_seconds=1200,
    )


register_adapter(SendAdapter(
    "E9-cad-to-scene", build_cad_to_scene,
    optional_params={
        "render_profile": OptionalParam(default="square-1024.v1", choices=SCENE_RENDER_PROFILES,
                                        description="โปรไฟล์การเรนเดอร์ภาพ"),
        "composition": OptionalParam(default=SCENE_COMPOSITIONS[0], choices=SCENE_COMPOSITIONS,
                                     description="มุมกล้องของฉาก"),
        "color": OptionalParam(default="#B8BEC6", description="สีวัสดุแบบ #RRGGBB"),
    },
))


GAME_AUDIO_ROLES = ("music", "collect_sfx")
GAME_LAYOUT = {
    "template": "top-down-collect.v1",
    "palette": "palette-minimal.v1",
    "world": {"width": 12, "height": 8, "tile_size": 1.0},
    "player": {"id": "runner", "spawn": [1, 1], "speed": 4.0},
    "walls": [{"id": "north-pillar", "cells": [[4, 1], [4, 2]]}],
    "objectives": [{"id": "golden-key", "cell": [7, 2]}, {"id": "silver-key", "cell": [2, 6]}],
    "exit": {"id": "moon-gate", "cell": [10, 5]},
}


def find_unity() -> str | None:
    for variable in ("PRODUCTION_HUB_UNITY", "UNITY_BIN"):
        configured = os.environ.get(variable)
        if configured and Path(configured).is_file():
            return configured
    default = Path("C:/Program Files/Unity/Hub/Editor/6000.6.2f1/Editor/Unity.exe")
    return str(default) if default.is_file() else None


def game_output_root() -> Path:
    """Short, shared output root: Unity's Library database breaks under deep Windows paths."""
    configured = os.environ.get("PRODUCTION_HUB_GAME_OUTPUT")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "BookProduction" / "Game"


def build_audio_to_game(ctx: SendContext) -> ToolRun:
    """E12b: a mastered track becomes the music (or collect sound) of the collection-room game (game-ir.v2)."""
    unity = find_unity()
    if unity is None:
        raise SendError("Unity 6000.6.2f1 was not found; set PRODUCTION_HUB_UNITY")
    role = ctx.params.get("role", "music")
    if role not in GAME_AUDIO_ROLES:
        raise SendError(f"role must be one of {', '.join(GAME_AUDIO_ROLES)}")
    scene_id = _slug(ctx.params["scene_id"], "")[:64].strip("-")
    if not scene_id:
        raise SendError("scene_id must contain letters or digits")
    source = ctx.artifacts[0].path
    if source.suffix.lower() not in (".wav", ".ogg"):
        raise SendError("the audio must be WAV or Ogg")
    asset_root = ctx.work_dir / "game-assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    filename = f"{role.replace('_', '-')}{source.suffix.lower()}"
    shutil.copyfile(source, asset_root / filename)
    asset = {"asset_id": role.replace("_", "-"), "kind": "audio", "filename": filename,
             "sha256": ctx.artifacts[0].artifact_id.removeprefix("sha256:")}
    game = {
        "schema_version": "game-ir.v2", "scene_id": scene_id, **GAME_LAYOUT, "assets": [asset],
        "audio": {"music": asset["asset_id"] if role == "music" else None,
                  "collect_sfx": asset["asset_id"] if role == "collect_sfx" else None},
    }
    game_path = ctx.work_dir / "game.json"
    game_path.write_text(json.dumps(game, indent=2), encoding="utf-8")
    out = game_output_root()

    def manifest() -> Path:
        # the output root is shared and content-addressed, so read which build this run produced or replayed
        try:
            report = json.loads((ctx.work_dir / "tool-stdout.log").read_text(encoding="utf-8").strip().splitlines()[-1])
            return Path(report["artifact_dir"]) / "qa-manifest.json"
        except (OSError, IndexError, ValueError, KeyError, TypeError) as exc:
            raise FileNotFoundError(f"game build did not report its artifact: {exc}") from exc

    return ToolRun(
        argv=[_bpp_python(), "-m", "book_production_platform.cli", "game", "build", "--game", str(game_path),
              "--output-root", str(out), "--unity", unity, "--asset-root", str(asset_root), "--timeout-s", "120"],
        cwd=ctx.work_dir, env=_bpp_env(), manifest_path=manifest, timeout_seconds=300,
    )


register_adapter(SendAdapter(
    "E12b-audio-to-game", build_audio_to_game, required_params=frozenset({"scene_id"}),
    optional_params={
        "role": OptionalParam(default="music", choices=GAME_AUDIO_ROLES, description="บทบาทเสียงในเกม"),
    },
))
