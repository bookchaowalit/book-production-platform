"""Ready-made hub-flow.v1 templates for the console (Production Hub 1.3).

A template names artifact slots (kind + label) and one flat parameter map;
:meth:`FlowTemplate.build` turns user values into a complete hub-flow.v1
definition, routing each parameter to the step that consumes it, so the
console never asks the user to write flow JSON. ``validate_flow`` and the
send adapters keep the final word at load and run time.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .flows import FLOW_SCHEMA_VERSION, FlowError, validate_flow
from .send import SEND_ADAPTERS, choice_problem

_ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class TemplateInput:
    """One artifact slot the user picks from the library."""

    name: str
    kind: str
    label: str
    required: bool = True


@dataclass(frozen=True)
class TemplateParam:
    required: bool = False
    choices: tuple[str, ...] = ()
    default: str = ""
    description: str = ""


@dataclass(frozen=True)
class FlowTemplate:
    id: str
    label: str
    description: str
    inputs: tuple[TemplateInput, ...]
    params: Mapping[str, TemplateParam]
    build: Callable[[str, Mapping[str, str], Mapping[str, str]], dict[str, Any]]


def _artifact_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _SHA.match(text):
        raise FlowError(f"template input {label} must be a registered artifact id (sha256:...)")
    return text


def _flow_id(template_id: str) -> str:
    return f"{template_id}-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


def _routed(params: Mapping[str, str], routes: Mapping[str, str], step: str) -> dict[str, str]:
    return {name: str(params[name]) for name, target in routes.items() if target == step and name in params}


def _checked(params: Mapping[str, str], routes: Mapping[str, str], step: str, handoff: str,
             defaults: Mapping[str, str] | None = None) -> dict[str, str]:
    """Route one step's params and reject anything the adapter would refuse."""
    step_params = {**(dict(defaults) if defaults else {}), **_routed(params, routes, step)}
    adapter = SEND_ADAPTERS.get(handoff)
    if adapter is None:
        raise FlowError(f"template step {step} uses handoff {handoff} which has no send adapter")
    missing = sorted(adapter.required_params - step_params.keys())
    if missing:
        raise FlowError(f"template needs parameter(s): {', '.join(missing)}")
    problem = choice_problem(adapter, step_params)
    if problem:
        raise FlowError(problem)
    return step_params


def _promote_build(project_id: str, inputs: Mapping[str, str], params: Mapping[str, str]) -> dict[str, Any]:
    routes = {
        "campaign_name": "captions-to-copy", "cues": "captions-to-copy", "channels": "captions-to-copy",
        "headline": "copy-to-graphic", "template": "copy-to-graphic", "variants": "copy-to-graphic",
        "author": "copy-to-graphic", "cta": "copy-to-graphic",
        "title": "copy-to-web", "cta_label": "copy-to-web", "features": "copy-to-web",
        "page_id": "copy-to-web", "slug": "copy-to-web", "footer": "copy-to-web",
    }
    channels = params.get("channels", "facebook")
    if "," in channels:
        raise FlowError("the promote-product template posts to one channel per flow; run it again for each channel")
    definition = {
        "schema_version": FLOW_SCHEMA_VERSION,
        "flow_id": _flow_id("promote-product"),
        "project_id": project_id,
        "steps": [
            {"id": "captions-to-copy", "send": {
                "handoff": "E2-captions-to-marketing", "input": {"artifact": _artifact_id(inputs.get("captions"), "captions")},
                "params": _checked(params, routes, "captions-to-copy", "E2-captions-to-marketing",
                                   defaults={"channels": "facebook"}),
            }},
            {"id": "copy-to-graphic", "send": {
                "handoff": "E6-copy-to-graphics", "input": {"from_step": "captions-to-copy", "kind": "copy.text"},
                "params": _checked(params, routes, "copy-to-graphic", "E6-copy-to-graphics"),
            }},
            {"id": "approve-graphic", "gate": {"from_step": "copy-to-graphic", "approve_kinds": ["graphic.png"]}},
            {"id": "copy-to-web", "send": {
                "handoff": "E7a-copy-to-web", "input": {"from_step": "captions-to-copy", "kind": "copy.text"},
                "params": _checked(params, routes, "copy-to-web", "E7a-copy-to-web"),
            }},
        ],
    }
    return definition


def _cad_build(project_id: str, inputs: Mapping[str, str], params: Mapping[str, str]) -> dict[str, Any]:
    routes = {
        "title": "cad-to-spec", "customer_name": "cad-to-spec", "locale": "cad-to-spec",
        "subject": "cad-to-spec", "notes": "cad-to-spec",
        "composition": "cad-to-scene", "render_profile": "cad-to-scene", "color": "cad-to-scene",
    }
    return {
        "schema_version": FLOW_SCHEMA_VERSION,
        "flow_id": _flow_id("cad-render-and-spec"),
        "project_id": project_id,
        "steps": [
            {"id": "cad-to-spec", "send": {
                "handoff": "E10-cad-to-documents", "input": {"artifact": _artifact_id(inputs.get("fcstd"), "fcstd")},
                "with": [{"artifact": _artifact_id(inputs.get("qa_manifest"), "qa_manifest")}],
                "params": _checked(params, routes, "cad-to-spec", "E10-cad-to-documents"),
            }},
            {"id": "cad-to-scene", "send": {
                "handoff": "E9-cad-to-scene", "input": {"artifact": _artifact_id(inputs.get("stl"), "stl")},
                "params": _checked(params, routes, "cad-to-scene", "E9-cad-to-scene"),
            }},
            {"id": "approve-render", "gate": {"from_step": "cad-to-scene", "approve_kinds": ["image.render"]}},
        ],
    }


def _game_build(project_id: str, inputs: Mapping[str, str], params: Mapping[str, str]) -> dict[str, Any]:
    routes = {"scene_id": "audio-to-game", "role": "audio-to-game"}
    return {
        "schema_version": FLOW_SCHEMA_VERSION,
        "flow_id": _flow_id("audio-to-game"),
        "project_id": project_id,
        "steps": [
            {"id": "audio-to-game", "send": {
                "handoff": "E12b-audio-to-game", "input": {"artifact": _artifact_id(inputs.get("audio"), "audio")},
                "params": _checked(params, routes, "audio-to-game", "E12b-audio-to-game"),
            }},
        ],
    }


FLOW_TEMPLATES: dict[str, FlowTemplate] = {
    template.id: template
    for template in (
        FlowTemplate(
            "promote-product", "โปรโมตสินค้า", "คำบรรยายจากคลิป (SRT) → copy → ภาพโพสต์ → จุดตรวจ → หน้าเว็บ",
            (TemplateInput("captions", "captions.srt", "ไฟล์คำบรรยาย (.srt)"),),
            {
                "campaign_name": TemplateParam(required=True, description="ชื่อแคมเปญ"),
                "headline": TemplateParam(required=True, description="พาดหัวภาพโพสต์"),
                "title": TemplateParam(required=True, description="ชื่อหน้าเว็บ"),
                "cta_label": TemplateParam(required=True, description="ข้อความปุ่มบนหน้าเว็บ"),
                "features": TemplateParam(required=True, description="จุดเด่น รูปแบบ 'หัวข้อ:รายละเอียด;...' 1-6 ข้อ"),
                "channels": TemplateParam(default="facebook", choices=("facebook", "instagram", "linkedin", "email", "line", "web"),
                                          description="โพสต์ช่องทางเดียวต่อ flow หนึ่ง"),
                "cues": TemplateParam(default="1-3", description="ช่วง cue ที่ใช้ เช่น 1-3"),
                "template": TemplateParam(default="announcement", choices=("announcement", "quote_card"), description="แม่แบบภาพ"),
                "variants": TemplateParam(default="square", choices=("square", "portrait", "story"), description="รูปทรงภาพ"),
                "author": TemplateParam(description="ชื่อผู้พูด (quote_card)"),
                "cta": TemplateParam(default="ดูเพิ่มเติม", description="ข้อความปุ่มบนภาพ"),
                "page_id": TemplateParam(description="รหัสหน้าเว็บ (ค่าเริ่มต้น: อัตโนมัติ)"),
                "slug": TemplateParam(description="slug ของ URL"),
                "footer": TemplateParam(description="ข้อความท้ายหน้า"),
            },
            _promote_build,
        ),
        FlowTemplate(
            "cad-render-and-spec", "CAD → spec sheet + ภาพ 3D", "แบบ CAD ออกเอกสารสเปก และเรนเดอร์ภาพสินค้ารอตรวจ",
            (
                TemplateInput("stl", "cad.stl", "ไฟล์ STL สำหรับเรนเดอร์"),
                TemplateInput("fcstd", "cad.fcstd", "ไฟล์ FCStd สำหรับ spec sheet"),
                TemplateInput("qa_manifest", "qa.manifest", "แฟ้มตรวจ CAD (qa-manifest.json)"),
            ),
            {
                "title": TemplateParam(required=True, description="ชื่อเอกสาร"),
                "customer_name": TemplateParam(required=True, description="ชื่อลูกค้า"),
                "locale": TemplateParam(default="th-TH", description="ภาษาเอกสาร"),
                "subject": TemplateParam(description="หัวข้อเอกสาร"),
                "notes": TemplateParam(description="หมายเหตุ คั่นบรรทัดด้วย |"),
                "composition": TemplateParam(default="three-quarter-fit.v1",
                                             choices=("three-quarter-fit.v1", "front-fit.v1", "side-fit.v1", "top-fit.v1", "three-quarter.v1"),
                                             description="มุมกล้อง"),
                "render_profile": TemplateParam(default="square-1024.v1", choices=("square-1024.v1", "landscape-1920x1080.v1"),
                                                description="โปรไฟล์ภาพ"),
                "color": TemplateParam(default="#B8BEC6", description="สีวัสดุ #RRGGBB"),
            },
            _cad_build,
        ),
        FlowTemplate(
            "audio-to-game", "เสียง → เกม", "นำเสียงที่มาสเตอร์แล้วเข้าเกมเก็บของเป็นเพลงหรือเสียงเก็บของ",
            (TemplateInput("audio", "audio.master", "ไฟล์เสียงที่มาสเตอร์แล้ว"),),
            {
                "scene_id": TemplateParam(required=True, description="รหัสฉากเกม (a-z, 0-9, -)"),
                "role": TemplateParam(default="music", choices=("music", "collect_sfx"), description="บทบาทเสียงในเกม"),
            },
            _game_build,
        ),
    )
}


def template_views() -> list[dict[str, Any]]:
    """JSON-shaped template metadata for the console."""
    return [
        {
            "id": template.id, "label": template.label, "description": template.description,
            "inputs": [{"name": item.name, "kind": item.kind, "label": item.label, "required": item.required}
                       for item in template.inputs],
            "params": {
                name: {"required": param.required, "choices": list(param.choices),
                       "default": param.default, "description": param.description}
                for name, param in template.params.items()
            },
        }
        for template in FLOW_TEMPLATES.values()
    ]


def build_template(template_id: str, project_id: str, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Build and validate one template run; raises FlowError with the reason."""
    template = FLOW_TEMPLATES.get(template_id)
    if template is None:
        raise FlowError(f"unknown flow template: {template_id}")
    clean_params = {str(key): str(value) for key, value in dict(params or {}).items()}
    unknown = sorted(clean_params.keys() - template.params.keys())
    if unknown:
        raise FlowError(f"template {template_id} does not take parameter(s): {', '.join(unknown)}")
    missing = sorted(name for name, param in template.params.items() if param.required and not clean_params.get(name))
    if missing:
        raise FlowError(f"template needs parameter(s): {', '.join(missing)}")
    for name, value in clean_params.items():
        choices = template.params[name].choices
        if choices and value.strip() not in choices:
            raise FlowError(f"{name} must be one of {', '.join(choices)}")
    project = str(project_id or "").strip() or template.id
    if not _ID.match(project):
        raise FlowError("project_id must be lowercase letters, digits, - or _")
    clean_inputs = {str(key): str(value) for key, value in dict(inputs or {}).items()}
    for item in template.inputs:
        if item.required and not clean_inputs.get(item.name):
            raise FlowError(f"template needs input {item.name} ({item.label})")
    definition = template.build(project, clean_inputs, clean_params)
    errors = validate_flow(definition)
    if errors:
        raise FlowError("; ".join(errors))
    return definition
