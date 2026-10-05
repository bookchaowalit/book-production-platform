"""CLI entry point for the local Graphics V1A product."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .export import ExportError, export_batch
from .jobs import IdempotencyConflict, run_batch
from .provider import (
    PROVIDER_NOT_CONFIGURED,
    SHADOW_MODE_ONLY,
    ProviderBriefAdapter,
    ProviderNotConfigured,
)
from .provider_config import ProviderConfigError, load_provider_config
from .render import RenderError

DEFAULT_BUDGET_ROOT = ".runtime/graphics-production-ai-budget"


def _provider_adapter(config_path: str | None, budget_root: str) -> ProviderBriefAdapter:
    if config_path is None:
        return ProviderBriefAdapter()
    try:
        config = load_provider_config(config_path)
    except ProviderConfigError as exc:
        raise ProviderNotConfigured(f"{PROVIDER_NOT_CONFIGURED}: {exc}") from None
    return ProviderBriefAdapter(provider_config=config, ledger_root=budget_root)


def _run_brief(args: argparse.Namespace) -> int:
    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if adapter.mode != "live":
        print(f"{SHADOW_MODE_ONLY}: the provider config is in shadow mode; run brief-shadow and get owner approval before mode=live", file=sys.stderr)
        return 2
    try:
        envelope: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict) or not isinstance(envelope.get("brief"), str):
        print("input error: expected a JSON object with a brief string", file=sys.stderr)
        return 2
    result = adapter.propose(envelope["brief"], job_id=args.job_id, locale=str(envelope.get("locale", "th-TH")))
    summary = {key: value for key, value in result.to_dict().items() if key != "batch"}
    if result.status == "complete" and result.batch is not None:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Metadata only (adapter, calls, chosen templates): ``batch`` copies it
        # into the job trace so the history shows the items were AI-proposed.
        proposal = {
            "adapter": result.adapter,
            "adapter_version": result.adapter_version,
            "mode": adapter.mode,
            "model_calls": result.model_calls,
            "templates": list(result.templates),
            "variants": list(result.variants),
            "shorten_requested": result.shorten_requested,
        }
        output.write_text(json.dumps({**result.batch, "proposal": proposal}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["batch_path"] = str(output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.status == "needs_input":
        print(f"needs_input: provide {', '.join(result.missing) or 'a clearer brief'}", file=sys.stderr)
        return 4
    if result.status != "complete":
        print(f"validation error: {result.issues[0]['code'] if result.issues else 'rejected'}", file=sys.stderr)
        return 2
    return 0


def _run_shadow(args: argparse.Namespace) -> int:
    from .shadow import load_cases, load_split, run_shadow

    try:
        adapter = _provider_adapter(args.provider_config, args.budget_root)
    except ProviderNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.cases)
        dev_ids = load_split(args.split) if args.split else []
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = run_shadow(adapter, cases, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphics-production")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("batch", help="render a JSON template batch")
    batch.add_argument("--input", required=True, help="path to a JSON object with an items array")
    batch.add_argument("--output", required=True, help="local output directory")
    batch.add_argument("--idempotency-key", required=True)
    export = commands.add_parser("export", help="print export (high-resolution PNG + PDF) of an accepted batch")
    export.add_argument("--manifest", required=True, help="path to a graphics-batch.v1 manifest")
    export.add_argument("--output", required=True, help="local output directory")
    export.add_argument("--scale", type=int, default=3, help="render scale factor (1-6)")
    export.add_argument("--dpi", type=int, default=300, help="PDF resolution (72-1200)")
    brief = commands.add_parser("brief", help="propose a template batch from a free-text brief (provider mode=live only)")
    brief.add_argument("--input", required=True, help="path to a JSON object with a brief string and optional locale")
    brief.add_argument("--output", required=True, help="path for the proposed batch JSON (input for the batch command)")
    brief.add_argument("--job-id", required=True, help="unique job id; the ledger allows at most two calls per job")
    brief.add_argument("--provider-config", default=None, help="graphics-provider-config.v1 JSON (no secrets)")
    brief.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    commands.add_parser("themes", help="list the registered templates (themes): sizes, text slots, and brief words")
    make = commands.add_parser("make", help="render one theme from command-line text (no JSON, no AI)")
    make.add_argument("--theme", required=True, help="template/theme id (see the themes command)")
    make.add_argument("--variant", action="append", default=None, help="square, portrait, story, landscape, or all; repeatable")
    make.add_argument("--set", dest="slots", action="append", default=[], metavar="SLOT=TEXT", help="slot text; repeatable")
    make.add_argument("--art-style", default=None, help="art style pack id (see the styles command), e.g. retro, minimal, jazz")
    make.add_argument("--keep-brand-colors", action="store_true", help="with --art-style: keep the brand colors as accents")
    make.add_argument("--photo", action="append", default=[], metavar="KEY=PATH", help="a local photo to place; repeatable")
    make.add_argument("--place", action="append", default=[], metavar="JSON",
                      help='photo placement JSON, e.g. \'{"id":"dog","asset":"dog","x":0.55,"y":0.55,"width":0.4,"height":0.4,'
                           '"z":"front","effects":{"cutout":"ai","fit":"contain","outline_width":12}}\'; repeatable')
    make.add_argument("--primary", default="#14213D", help="brand primary color")
    make.add_argument("--accent", default="#FCA311", help="brand accent color")
    make.add_argument("--output", required=True, help="local output directory")
    make.add_argument("--idempotency-key", required=True)
    controls = commands.add_parser("controls", help="list a request's adjustable layers, items, palette, and styles")
    controls.add_argument("--request", required=True, help="graphic request JSON (for example design-request.json from edit)")
    edit = commands.add_parser("edit", help="change a design from a plain-language instruction via the AI gateway (mode=live only)")
    edit.add_argument("--request", required=True, help="graphic request JSON to edit (edits accumulate in design-request.json)")
    edit.add_argument("--instruction", default=None, help="what to change, in plain language")
    edit.add_argument("--patch", default=None, help="structured change-set JSON instead of an instruction (no AI)")
    edit.add_argument("--output", required=True, help="local output directory (design-request.json, SVG, PNG)")
    edit.add_argument("--job-id", required=True, help="unique job id; the ledger allows at most two calls per job")
    edit.add_argument("--locale", default="th-TH")
    edit.add_argument("--provider-config", default=None, help="graphics design-edit provider config JSON (no secrets)")
    edit.add_argument("--budget-root", default=".runtime/graphics-design-edit-ai-budget", help="persistent AI cost ledger directory")
    edit.add_argument("--style-provider-config", default=None,
                      help="art-style designer provider config JSON; lets the editor create a new look on request")
    edit.add_argument("--style-budget-root", default=".runtime/graphics-art-style-ai-budget")
    commands.add_parser("styles", help="list the art style packs (built-in and the style library)")
    style_create = commands.add_parser("style-create", help="design a new art style pack from a description via the AI gateway (mode=live only)")
    style_create.add_argument("--description", required=True, help="the look, in any words (e.g. 'งานวัดไทย ไฟกระพริบ สีสด')")
    style_create.add_argument("--job-id", required=True)
    style_create.add_argument("--locale", default="th-TH")
    style_create.add_argument("--provider-config", required=True, help="art-style provider config JSON (no secrets)")
    style_create.add_argument("--budget-root", default=".runtime/graphics-art-style-ai-budget")
    style_create.add_argument("--output", default=None, help="optional directory for a preview batch in the new style")
    edit_shadow = commands.add_parser("edit-shadow", help="shadow-evaluate the AI design editor; never renders or uses results")
    edit_shadow.add_argument("--provider-config", required=True)
    edit_shadow.add_argument("--cases", action="append", required=True)
    edit_shadow.add_argument("--split", default=None)
    edit_shadow.add_argument("--budget-root", default=".runtime/graphics-design-edit-ai-budget")
    edit_shadow.add_argument("--output", required=True)
    shadow = commands.add_parser("brief-shadow", help="shadow-evaluate the brief adapter; never renders or uses results")
    shadow.add_argument("--provider-config", required=True, help="graphics-provider-config.v1 JSON (no secrets)")
    shadow.add_argument("--cases", action="append", required=True, help="eval case JSON file; repeatable")
    shadow.add_argument("--split", default=None, help="eval split JSON naming dev case IDs")
    shadow.add_argument("--budget-root", default=DEFAULT_BUDGET_ROOT, help="persistent AI cost ledger directory")
    shadow.add_argument("--output", required=True, help="path for the metadata-only shadow report JSON")
    return parser


def _make_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    from .themes import VARIANT_SIZES, get_theme

    theme = get_theme(args.theme)
    requested = args.variant or ["all"]
    variants = list(theme.variants) if "all" in requested else requested
    content: dict[str, str] = {}
    for entry in args.slots:
        slot, separator, text = entry.partition("=")
        if not separator or slot not in theme.slots:
            raise ValueError(f"unknown slot {slot!r}; {args.theme} takes: {', '.join(theme.slots)}")
        content[slot] = text
    design: dict[str, Any] = {}
    if args.art_style:
        design["art_style"] = args.art_style
        design["keep_brand_colors"] = bool(args.keep_brand_colors)
    photos: dict[str, Path] = {}
    for entry in args.photo:
        key, separator, path = entry.partition("=")
        if not separator or not key or not Path(path).is_file():
            raise ValueError(f"--photo needs KEY=PATH to an existing file: {entry!r}")
        photos[key] = Path(path).resolve()
    if args.place:
        design["images"] = [json.loads(place) for place in args.place]
    extra: dict[str, Any] = {}
    if photos:
        root = Path(os.path.commonpath([str(path.parent) for path in photos.values()]))
        extra = {"asset_root": str(root), "assets": {key: path.relative_to(root).as_posix() for key, path in photos.items()}}
    items = []
    for variant in variants:
        if variant not in theme.variants:
            raise ValueError(f"{args.theme} does not come in {variant}; sizes: {', '.join(theme.variants)}")
        width, height = VARIANT_SIZES[variant]
        item: dict[str, Any] = {
            "request_id": f"{args.theme}-{variant}", "template_id": args.theme, "variant": variant,
            "canvas": {"width": width, "height": height}, "brand": {"primary": args.primary, "accent": args.accent},
            "content": content, **extra,
        }
        if design:
            item["design"] = design
        items.append(item)
    return items


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_edit(args: argparse.Namespace) -> int:
    from .design_edit import DesignEditor, EditorNotConfigured, apply_change

    try:
        request = _read_json(args.request)
        patch = _read_json(args.patch) if args.patch else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if (patch is None) == (args.instruction is None):
        print("input error: give exactly one of --instruction or --patch", file=sys.stderr)
        return 2
    if patch is not None:
        result = apply_change(request, patch)
    else:
        try:
            config = load_provider_config(args.provider_config) if args.provider_config else None
            designer = None
            if args.style_provider_config:
                from .art_style_ai import ArtStyleDesigner

                designer = ArtStyleDesigner(provider_config=load_provider_config(args.style_provider_config),
                                            ledger_root=args.style_budget_root)
                if designer.mode != "live":
                    designer = None  # a shadow-mode designer never creates styles for real jobs
            editor = DesignEditor(provider_config=config, ledger_root=args.budget_root, style_designer=designer)
        except (EditorNotConfigured, ProviderConfigError) as exc:
            print(f"provider_not_configured: {exc}", file=sys.stderr)
            return 2
        if editor.mode != "live":
            print("shadow_mode_only: the design-edit config is in shadow mode; run edit-shadow and get owner approval before mode=live", file=sys.stderr)
            return 2
        result = editor.edit(request, args.instruction, job_id=args.job_id, locale=args.locale)
    summary: dict[str, Any] = {key: value for key, value in result.to_dict().items() if key != "request"}
    if result.status != "complete" or result.request is None:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 4 if result.status == "needs_input" else 2
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "design-request.json").write_text(json.dumps(result.request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    batch = run_batch([result.request], output, f"edit-{args.job_id}")
    summary["batch"] = {"status": batch.status, "manifest": str(batch.manifest_path) if batch.manifest_path else None}
    summary["design_request"] = str(output / "design-request.json")
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if batch.status == "accepted" else 2


def _run_edit_shadow(args: argparse.Namespace) -> int:
    from book_production_platform.brief_adapter_kit.shadow import load_cases

    from .design_edit import DesignEditor, EditorNotConfigured
    from .design_edit_shadow import run_edit_shadow

    try:
        editor = DesignEditor(provider_config=load_provider_config(args.provider_config), ledger_root=args.budget_root)
        cases = load_cases(args.cases)
        dev_ids = _read_json(args.split)["dev"] if args.split else []
    except (EditorNotConfigured, ProviderConfigError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    report = run_edit_shadow(editor, cases, dev_ids=dev_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        print("shadow gate failed: keep mode=shadow and review the report", file=sys.stderr)
        return 6
    return 0


def _run_style_create(args: argparse.Namespace) -> int:
    from .art_style_ai import ArtStyleDesigner, StyleDesignerNotConfigured

    try:
        designer = ArtStyleDesigner(provider_config=load_provider_config(args.provider_config), ledger_root=args.budget_root)
    except (StyleDesignerNotConfigured, ProviderConfigError) as exc:
        print(f"provider_not_configured: {exc}", file=sys.stderr)
        return 2
    if designer.mode != "live":
        print("shadow_mode_only: the art-style config is in shadow mode; get owner approval before mode=live", file=sys.stderr)
        return 2
    result = designer.design(args.description, job_id=args.job_id, locale=args.locale)
    summary = result.to_dict()
    if result.status == "complete" and result.style is not None and args.output:
        from .art_style_ai import SAMPLE_REQUESTS
        from .themes import VARIANT_SIZES

        items = []
        for index, sample in enumerate(SAMPLE_REQUESTS):
            width, height = VARIANT_SIZES[sample["variant"]]
            items.append({"request_id": f"{result.style.style_id}-preview-{index + 1}", "template_id": sample["template_id"],
                          "variant": sample["variant"], "canvas": {"width": width, "height": height},
                          "brand": {"primary": "#3B2A20", "accent": "#C9772E"}, "content": sample["content"],
                          "design": {"art_style": result.style.style_id}})
        batch = run_batch(items, args.output, f"style-{args.job_id}")
        summary["preview"] = {"status": batch.status, "manifest": str(batch.manifest_path) if batch.manifest_path else None}
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "complete" else 4 if result.status == "needs_input" else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "styles":
        from .art_styles import load_art_styles

        print(json.dumps([style.summary() for style in load_art_styles().values()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "style-create":
        return _run_style_create(args)
    if args.command == "controls":
        from .templates import TemplateError, controls_for

        try:
            print(json.dumps(controls_for(_read_json(args.request)), ensure_ascii=False, indent=2))
        except (OSError, json.JSONDecodeError, TemplateError, ValueError) as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "edit":
        return _run_edit(args)
    if args.command == "edit-shadow":
        return _run_edit_shadow(args)
    if args.command == "themes":
        from .themes import load_themes

        print(json.dumps([theme.summary() for theme in load_themes().values()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "make":
        from .themes import ThemeError

        try:
            make_items = _make_items(args)
        except (ValueError, ThemeError, json.JSONDecodeError) as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return 2
        try:
            made = run_batch(make_items, args.output, args.idempotency_key)
        except IdempotencyConflict as exc:
            print(f"idempotency error: {exc}", file=sys.stderr)
            return 2
        # One request file per size: the starting point for `controls` and `edit`.
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        for item in make_items:
            (output / f"{item['request_id']}.request.json").write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(made.to_dict(), ensure_ascii=False, default=str, indent=2))
        return 0 if made.status in {"accepted", "partial"} else 2
    if args.command == "brief":
        return _run_brief(args)
    if args.command == "brief-shadow":
        return _run_shadow(args)
    if args.command == "export":
        try:
            exported = export_batch(args.manifest, args.output, scale=args.scale, dpi=args.dpi)
        except (ExportError, RenderError) as exc:
            print(f"export error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"manifest": str(exported.manifest_path), "items": exported.items}, ensure_ascii=False))
        return 0
    try:
        payload: Any = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        print("input error: expected an items array of objects", file=sys.stderr)
        return 2
    try:
        proposal = payload.get("proposal") if isinstance(payload, dict) else None
        result = run_batch(items, args.output, args.idempotency_key, ai_proposal=proposal if isinstance(proposal, dict) else None)
    except IdempotencyConflict as exc:
        print(f"idempotency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status in {"accepted", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
