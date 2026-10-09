"""Manual reproduction guide: how a person would make the same output by hand.

Each tool converts its validated IR into a :class:`ManualGuide` for a named
real program (for example DaVinci Resolve for video, Microsoft Excel for
workbooks, Figma for graphics). The guide lists the project setup, the layer /
track / sheet structure, and numbered steps naming the exact tool or menu,
where to click or drag, and which value to enter, followed by checks that the
result matches the engine's artifact. It is rendered to ``MANUAL-STEPS.md``.

Human-facing text is Thai by default (repository language policy); program UI
labels, menu paths, shortcuts, file names, and values stay exactly as the
program shows them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuideLayer:
    order: int
    name: str
    kind: str
    content: str = ""
    position: str = ""


@dataclass
class GuideStep:
    number: int
    action: str
    tool: str = ""
    target: str = ""
    value: str = ""
    note: str = ""
    section: str = "ขั้นตอน"


@dataclass
class ManualGuide:
    """A program-specific, step-by-step recipe derived from one job's IR."""

    program: str
    program_version: str = ""
    alternatives: list[str] = field(default_factory=list)
    title: str = ""
    summary: str = ""
    estimated_minutes: float | None = None
    setup: list[str] = field(default_factory=list)
    layers: list[GuideLayer] = field(default_factory=list)
    steps: list[GuideStep] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)

    def layer(self, name: str, kind: str, *, content: str = "", position: str = "") -> GuideLayer:
        item = GuideLayer(order=len(self.layers) + 1, name=name, kind=kind, content=content, position=position)
        self.layers.append(item)
        return item

    def step(self, action: str, *, tool: str = "", target: str = "", value: str = "", note: str = "", section: str = "ขั้นตอน") -> GuideStep:
        item = GuideStep(number=len(self.steps) + 1, action=action, tool=tool, target=target, value=value, note=note, section=section)
        self.steps.append(item)
        return item


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "—"


def render_manual_markdown(guide: ManualGuide, trace: Mapping[str, Any] | None = None) -> str:
    """Render the guide as Markdown; ``trace`` adds a provenance footer."""

    lines: list[str] = [f"# วิธีทำเองด้วย {guide.program}" + (f" {guide.program_version}" if guide.program_version else "")]
    if guide.title:
        lines += ["", f"**งาน:** {guide.title}"]
    if guide.summary:
        lines += ["", guide.summary]
    meta = []
    if guide.alternatives:
        meta.append(f"- โปรแกรมทางเลือก: {', '.join(guide.alternatives)}")
    if guide.estimated_minutes is not None:
        meta.append(f"- เวลาโดยประมาณถ้าทำเอง: ~{guide.estimated_minutes:g} นาที")
    if trace is not None:
        meta.append(f"- ผลลัพธ์อ้างอิง (ทำโดยเครื่องมือ): {', '.join(a['file'] for a in trace.get('artifacts', [])) or '—'}")
    if meta:
        lines += ["", *meta]
    if guide.setup:
        lines += ["", "## 1. เตรียมงาน", ""]
        lines += [f"{index}. {item}" for index, item in enumerate(guide.setup, start=1)]
    if guide.layers:
        lines += ["", "## 2. โครงสร้าง (layer / track / sheet)", "", "| ลำดับ | ชื่อ | ชนิด | เนื้อหา | ตำแหน่ง |", "|---|---|---|---|---|"]
        lines += [f"| {layer.order} | {_cell(layer.name)} | {_cell(layer.kind)} | {_cell(layer.content)} | {_cell(layer.position)} |" for layer in guide.layers]
    if guide.steps:
        lines += ["", "## 3. ขั้นตอนทีละคลิก"]
        current = None
        for step in guide.steps:
            if step.section != current:
                current = step.section
                lines += ["", f"### {current}", ""]
            detail = []
            if step.tool:
                detail.append(f"เครื่องมือ/เมนู: `{step.tool}`")
            if step.target:
                detail.append(f"ที่: {step.target}")
            if step.value:
                detail.append(f"ค่า: `{step.value}`")
            line = f"{step.number}. {step.action}"
            if detail:
                line += " — " + "; ".join(detail)
            lines.append(line)
            if step.note:
                lines.append(f"   - หมายเหตุ: {step.note}")
    if guide.checks:
        lines += ["", "## 4. ตรวจผลให้ตรงกับไฟล์ของเครื่องมือ", ""]
        lines += [f"- [ ] {item}" for item in guide.checks]
    if guide.glossary:
        lines += ["", "## คำศัพท์", ""]
        lines += [f"- **{term}**: {meaning}" for term, meaning in guide.glossary.items()]
    if trace is not None:
        lines += [
            "",
            "## ประวัติงาน (trace)",
            "",
            f"- tool: `{trace.get('tool_id')}` · job: `{trace.get('job_id')}` · สถานะ: `{trace.get('status')}`",
            f"- engine: `{trace.get('engine_version')}` · recipe: `{trace.get('recipe_version')}`",
            f"- AI: {'ใช้ ' + str(trace.get('ai', {}).get('model_calls', 0)) + ' ครั้ง' if trace.get('ai', {}).get('used') else 'ไม่ได้ใช้'}",
            f"- ขั้นตอนที่เครื่องมือทำ: {len(trace.get('steps', []))} ขั้น (ดูรายละเอียดใน `trace.json`)",
        ]
    return "\n".join(lines) + "\n"


__all__ = ["GuideLayer", "GuideStep", "ManualGuide", "render_manual_markdown"]
