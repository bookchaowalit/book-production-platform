"""Manual-reproduction guide for Workflow V1A: build the same process by hand.

Three real programs, one per engine artifact, all driven by the validated
``WorkflowIR`` and the same :class:`~workflow_production.bpmn.BpmnModel` the
``.bpmn`` export uses:

* **Google Forms** — the form fields (type, label, required, number
  validation) and a preview response with the IR's form values;
* **Camunda Modeler** (BPMN 2.0, Camunda 8 properties) — pool, one lane per
  role, every step node with the same ID/type/name/column, joins, the
  exclusive-gateway conditions and default flow, timers, retries, then the
  sequence flows in the engine's order;
* **Google Docs** — the rendered document (Heading 1 / Heading 2 / Normal /
  bulleted ``label: value`` lines), downloaded as Markdown.

A Power Automate mapping is listed as the alternative path. The decision
outcome (which branch is taken, which steps are skipped) is copied from the
engine's plan, never re-decided by hand.
"""

from __future__ import annotations

from production_trace import ManualGuide
from workflow_ir import FormField, WorkflowIR, route_workflow

from .bpmn import KIND_TYPES, BpmnModel, BpmnNode, build_bpmn_model
from .render import _display_value

PROGRAM = "Camunda Modeler"
FORM_TYPES = {
    "text": ("Short answer", ""),
    "integer": ("Short answer", "Response validation ▸ Number ▸ Whole number"),
    "decimal": ("Short answer", "Response validation ▸ Number ▸ Is number"),
    "date": ("Date", ""),
    "boolean": ("Multiple choice", "ตัวเลือก: Yes / No"),
}
CHANGE_ELEMENT = {
    "userTask": "User task",
    "serviceTask": "Service task",
    "exclusiveGateway": "Exclusive gateway",
    "inclusiveGateway": "Inclusive gateway",
    "endEvent": "End event",
}


def _form_value(field: FormField, ir: WorkflowIR) -> str:
    if field.field_id not in ir.form_values or ir.form_values[field.field_id] is None:
        return "(เว้นว่าง)"
    value = ir.form_values[field.field_id]
    if field.field_type == "boolean":
        return "Yes" if value else "No"
    return _display_value(value)


def _where(model: BpmnModel, node: BpmnNode) -> str:
    lane = model.lanes[node.lane]
    return f"lane `{lane.name}`, คอลัมน์ {node.column}" + (f" แถว {node.row + 1}" if node.row else "") + f" (x={node.x}, y={node.y})"


def _node_steps(guide: ManualGuide, model: BpmnModel, node: BpmnNode, section: str) -> None:
    where = _where(model, node)
    if node.type == "boundaryEvent":
        kind = "Timer boundary event" + (" (non-interrupting)" if not node.interrupting else "")
        guide.step(
            f"ลาก Intermediate/Boundary event ไปวางบนขอบล่างของ `{node.attached_to}` แล้วเปลี่ยนเป็น {kind}",
            tool=f"Palette ▸ Create intermediate/boundary event; Context pad ▸ 🔧 Change element ▸ {kind}",
            target=f"ขอบล่างของ {node.attached_to}",
            section=section,
        )
        guide.step("ตั้ง ID และเวลา SLA", tool="Properties panel ▸ General ▸ ID; Timer ▸ Type = Duration ▸ Value", value=f"ID {node.id}; {node.timer}", section=section)
        return
    if node.shape == "task":
        guide.step("วาง Task", tool="Palette ▸ Create task", target=where, section=section)
        guide.step("เปลี่ยนชนิด Task", tool=f"Context pad ▸ 🔧 Change element ▸ {CHANGE_ELEMENT[node.type]}", value=CHANGE_ELEMENT[node.type], section=section)
    elif node.shape == "gateway":
        guide.step("วาง Gateway", tool="Palette ▸ Create gateway", target=where, section=section)
        guide.step("เปลี่ยนชนิด Gateway", tool=f"Context pad ▸ 🔧 Change element ▸ {CHANGE_ELEMENT[node.type]}", value=CHANGE_ELEMENT[node.type], section=section)
    else:
        guide.step("วาง End event", tool="Palette ▸ Create end event", target=where, section=section)
    guide.step("ตั้ง ID และชื่อ", tool="Properties panel ▸ General ▸ ID / Name", value=f"ID {node.id}; Name {node.name or '(เว้นว่าง)'}", section=section)
    if node.task_type:
        guide.step(
            "ตั้ง Task definition และจำนวนครั้งที่ลองซ้ำ",
            tool="Properties panel ▸ Task definition ▸ Type / Retries",
            value=f"Type {node.task_type}; Retries {node.retries}",
            section=section,
        )
    if node.candidate_group:
        guide.step("กำหนดกลุ่มผู้รับงาน", tool="Properties panel ▸ Assignment ▸ Candidate groups", value=node.candidate_group, section=section)
    if node.documentation:
        guide.step("ใส่คำอธิบาย", tool="Properties panel ▸ Documentation ▸ Element documentation", value=node.documentation, section=section)


def build_workflow_guide(ir: WorkflowIR, *, plan_name: str, document_name: str, bpmn_name: str, model: BpmnModel | None = None) -> ManualGuide:
    """Translate a validated ``WorkflowIR`` into Google Forms + Camunda Modeler + Google Docs steps."""

    model = model or build_bpmn_model(ir)
    route = route_workflow(ir)
    field_by_id = {item.field_id: item for item in ir.fields}
    tasks = [node for node in model.nodes if node.shape == "task"]
    guide = ManualGuide(
        program=PROGRAM,
        program_version="5.x (BPMN 2.0, Camunda 8) + Google Forms + Google Docs",
        alternatives=["Microsoft Power Automate + Microsoft Forms", "draw.io (BPMN shapes)", "bpmn.io (เว็บ)"],
        title=ir.name,
        summary=(
            f"ทำตามขั้นตอนนี้จะได้ (1) แบบฟอร์มใน Google Forms (2) ผังกระบวนการ BPMN ใน Camunda Modeler ที่มี ID/ชนิด/lane/เส้นเชื่อม "
            f"เหมือน `{bpmn_name}` และ (3) เอกสารใน Google Docs เหมือน `{document_name}`; เส้นทางของ decision ให้คัดลอกจาก "
            f"`{plan_name}` ไม่ต้องตัดสินใหม่ (V1A วางแผนอย่างเดียว ไม่ได้รันจริง)"
        ),
        estimated_minutes=float(10 + 2 * len(ir.fields) + 3 * len(model.nodes) + len(model.flows) + 5 + len(ir.document.sections)),
        setup=[
            "เปิด https://forms.google.com ▸ `Blank form` (แท็บที่ 1)",
            "เปิด Camunda Modeler (desktop) ▸ `File ▸ New File ▸ BPMN diagram (Camunda 8)` (แท็บที่ 2)",
            "เปิด https://docs.google.com ▸ `Blank document` (แท็บที่ 3)",
            f"เตรียมไฟล์จากเครื่องมือไว้เทียบ: `{plan_name}`, `{bpmn_name}`, `{document_name}`",
            "ห้ามกด Send ฟอร์ม/Deploy ผัง — V1A ไม่มี external effect",
        ],
    )

    for index, form_field in enumerate(ir.fields, start=1):
        kind, validation = FORM_TYPES[form_field.field_type]
        guide.layer(f"Form Q{index}: {form_field.label}", f"Google Forms ▸ {kind}", content=form_field.field_id, position="Required" if form_field.required else "Optional")
    for lane in model.lanes:
        guide.layer(f"Lane: {lane.name}", "BPMN Lane", content=lane.id, position=f"y={lane.top}, height={lane.height}")
    for node in model.nodes:
        status = ""
        if node.step_id:
            status = "skipped" if node.step_id in route.skipped else "planned"
        guide.layer(node.name or node.id, f"BPMN {node.type}", content=node.id + (f" · {status}" if status else ""), position=_where(model, node))
    for flow in model.flows:
        label = flow.condition or ("default" if flow.is_default else "")
        guide.layer(f"{flow.source} → {flow.target}", "BPMN Sequence flow", content=label, position=flow.id)
    for index, section in enumerate(ir.document.sections, start=1):
        kind = {"heading": "Heading 2", "paragraph": "Normal text", "field": "Bulleted list"}[section.kind]
        guide.layer(f"Doc block {index}", f"Google Docs ▸ {kind}", content=section.text or section.field_id or "")

    s = "A. แบบฟอร์ม (Google Forms)"
    guide.step("ตั้งชื่อฟอร์ม", tool="Form title (บนสุด)", value=ir.name, section=s)
    for index, form_field in enumerate(ir.fields, start=1):
        kind, validation = FORM_TYPES[form_field.field_type]
        guide.step(f"เพิ่มคำถามที่ {index}", tool="Floating toolbar ▸ ⊕ Add question", section=s)
        guide.step("เลือกชนิดคำถาม", tool="Question type dropdown", value=kind, section=s)
        guide.step("พิมพ์ข้อความคำถาม", tool="Question", value=form_field.label, note=f"field_id ในเครื่องมือ: {form_field.field_id}", section=s)
        if validation:
            guide.step("ตั้งการตรวจคำตอบ", tool="⋮ ▸ Response validation" if validation.startswith("Response") else "Options", value=validation, section=s)
        guide.step("ตั้งว่าบังคับตอบหรือไม่", tool="Required toggle", value="On" if form_field.required else "Off", section=s)
    guide.step("กรอกคำตอบทดสอบด้วยค่าจากเครื่องมือ (ใช้ตัดสินเส้นทาง decision)", tool="👁 Preview", section=s)
    for form_field in ir.fields:
        guide.step(f"กรอก {form_field.label}", tool="Preview ▸ คำถาม", value=_form_value(form_field, ir), section=s)

    s = "B1. สร้าง pool และ lane (Camunda Modeler)"
    guide.step("วาง Pool/Participant บน canvas", tool="Palette ▸ Create pool/participant", target="ซ้ายบนของ canvas", value=f"x={model.pool[0]}, y={model.pool[1]}", section=s)
    guide.step("ตั้งชื่อ pool และ process", tool="Properties panel ▸ General ▸ Name; Process ▸ ID", value=f"Name {model.name}; Process ID {model.process_id}", section=s)
    guide.step("ปิด Executable (V1A วางแผนอย่างเดียว)", tool="Properties panel ▸ Process ▸ Executable", value="Off", section=s)
    for index, lane in enumerate(model.lanes):
        if index:
            guide.step("เพิ่ม lane ใต้ lane ล่าสุด", tool="เลือก lane ล่าสุด ▸ Context pad ▸ Add lane below", section=s)
        guide.step(f"ตั้งชื่อ lane ที่ {index + 1}", tool="Properties panel ▸ General ▸ Name", value=lane.name, section=s)

    s = "B2. วางโหนดทีละตัว (ตามลำดับของเครื่องมือ)"
    start = model.node("StartEvent_1")
    guide.step("ลาก Start event ที่มีอยู่แล้วเข้า lane แรก แล้วตั้งชื่อ", tool="Canvas ▸ StartEvent_1; Properties ▸ General ▸ Name", target=_where(model, start), value="Start", section=s)
    for node in model.nodes:
        if node.id != "StartEvent_1":
            _node_steps(guide, model, node, s)

    s = "B3. ลากเส้นเชื่อม (sequence flow) ตามลำดับ"
    for flow in model.flows:
        guide.step(
            f"เชื่อม `{flow.source}` → `{flow.target}`",
            tool="เลือกโหนดต้นทาง ▸ Context pad ▸ Connect using sequence flow ▸ คลิกโหนดปลายทาง",
            value=f"ID {flow.id}",
            section=s,
        )
        if flow.condition:
            guide.step("ใส่เงื่อนไขของเส้นนี้ (FEEL)", tool="Properties panel ▸ Condition ▸ Condition expression", value=flow.condition, section=s)
        if flow.is_default:
            guide.step("ตั้งเป็นเส้นทาง default ของ gateway", tool="เลือกเส้น ▸ Context pad ▸ 🔧 Change element ▸ Default flow", section=s)

    s = "B4. ตรวจและบันทึกผัง"
    guide.step("เปิดแผงตรวจปัญหาแล้วแก้จนไม่มี error", tool="Bottom bar ▸ Problems", value="0 errors", section=s)
    guide.step("บันทึกไฟล์", tool="File ▸ Save File As…", value=bpmn_name, section=s)
    guide.step("เปิดไฟล์ของเครื่องมือในแท็บใหม่เพื่อเทียบ", tool="File ▸ Open File…", value=bpmn_name, section=s)

    s = "C. เอกสารผลลัพธ์ (Google Docs)"
    guide.step("พิมพ์ชื่อเอกสาร แล้วเลือกสไตล์ Heading 1", tool="Format ▸ Paragraph styles ▸ Heading 1", value=ir.document.title, section=s)
    for section in ir.document.sections:
        if section.kind == "heading":
            guide.step("พิมพ์หัวข้อ แล้วเลือก Heading 2", tool="Format ▸ Paragraph styles ▸ Heading 2", value=section.text or "", section=s)
        elif section.kind == "paragraph":
            guide.step("พิมพ์ย่อหน้า (Normal text)", tool="Format ▸ Paragraph styles ▸ Normal text", value=section.text or "", section=s)
        else:
            form_field = field_by_id[section.field_id or ""]
            value = _display_value(ir.form_values[form_field.field_id]) if form_field.field_id in ir.form_values else "—"
            guide.step(
                "พิมพ์บรรทัด bullet ‘ชื่อช่อง: ค่า’ (ชื่อช่องตัวหนา)",
                tool="Format ▸ Bullets & numbering ▸ Bulleted list; Ctrl+B",
                value=f"{form_field.label}: {value}",
                section=s,
            )
    guide.step("ดาวน์โหลดเป็น Markdown แล้วเทียบกับไฟล์ของเครื่องมือ", tool="File ▸ Download ▸ Markdown (.md)", value=document_name, section=s)

    s = "D. ทางเลือก: Microsoft Power Automate"
    guide.step("สร้าง flow ใหม่ที่เริ่มจากฟอร์ม", tool="Create ▸ Automated cloud flow ▸ When a new response is submitted (Microsoft Forms)", value=ir.name, section=s)
    guide.step("ดึงคำตอบ", tool="+ New step ▸ Microsoft Forms ▸ Get response details", section=s)
    for step in ir.steps:
        if step.kind == "collect_form":
            continue
        if step.kind == "human_review":
            guide.step(f"เพิ่มการอนุมัติ `{step.step_id}`", tool="+ New step ▸ Approvals ▸ Start and wait for an approval", value=f"Approve/Reject - First to respond; Assigned to: กลุ่ม {step.role}", section=s)
        elif step.kind == "decision":
            guide.step(f"เพิ่มเงื่อนไข `{step.step_id}` (เงื่อนไขแรกที่ตรงชนะ ที่เหลือไป default)", tool="+ New step ▸ Control ▸ Condition / Switch", value="; ".join(f"{branch.field_id} {branch.operator} {branch.value} → {branch.target}" for branch in step.branches) + f"; default → {step.default_target}", section=s)
        else:
            guide.step(f"เพิ่ม action `{step.step_id}`", tool="+ New step ▸ " + ("Control ▸ Condition (ตรวจค่า)" if step.kind == "validate" else "Word Online ▸ Populate a Microsoft Word template"), section=s)
        if step.retry.max_attempts > 1:
            guide.step("ตั้งการลองซ้ำ (Count = จำนวนครั้งที่ลองซ้ำ ไม่รวมครั้งแรก)", tool="⋯ ▸ Settings ▸ Retry policy ▸ Fixed interval", value=f"Count {step.retry.max_attempts - 1}", section=s)
        if step.timeout_minutes:
            guide.step("ตั้ง SLA", tool="⋯ ▸ Settings ▸ Timeout", value=f"PT{step.timeout_minutes}M", note=f"เมื่อหมดเวลาให้ส่งต่อ {step.escalate_to}: Configure run after ▸ has timed out" if step.escalate_to else "", section=s)

    counts: dict[str, int] = {}
    for node in model.nodes:
        counts[node.type] = counts.get(node.type, 0) + 1
    guide.checks = [
        f"ฟอร์มมี {len(ir.fields)} คำถาม (บังคับตอบ {sum(item.required for item in ir.fields)} ข้อ) ตามลำดับในตาราง",
        f"ผังมี {len(model.lanes)} lane: " + ", ".join(lane.name for lane in model.lanes),
        "จำนวนโหนดตามชนิด: " + ", ".join(f"{name} {count}" for name, count in sorted(counts.items())),
        f"มี sequence flow {len(model.flows)} เส้น และ ID ตรงกับ `{bpmn_name}`",
        f"ทุกขั้นใน `{plan_name}` มีโหนด ID เดียวกันใน BPMN และชนิดตรงกัน ({', '.join(f'{kind}→{bpmn}' for kind, bpmn in KIND_TYPES.items())})",
        *(f"decision `{decision}` ไปทาง `{target}` (จาก plan)" for decision, target in sorted(route.selected.items())),
        "ขั้นที่ถูกข้าม (skipped): " + (", ".join(sorted(route.skipped)) or "ไม่มี"),
        *(f"`{node.attached_to}` มี timer {node.timer}" + (" แบบ non-interrupting" if not node.interrupting else "") for node in model.nodes if node.timer),
        *(f"`{node.id}` Retries = {node.retries}" for node in tasks if node.task_type),
        "Camunda Modeler ▸ Problems = 0 errors; process ปิด Executable",
        f"เอกสาร: Heading 1 = {ir.document.title}; มี {len(ir.document.sections)} บล็อกตามลำดับ",
        "QA ของเครื่องมือ: plan_only, external_effects = false, model_calls = 0, retry ≤ 3, render_document ไม่ถูกข้าม",
    ]
    guide.glossary = {
        "Lane": "แถบแนวนอนใน pool แทนบทบาท (role) ผู้ทำงาน",
        "Exclusive gateway": "จุดตัดสินใจ เลือกได้ทางเดียว (เงื่อนไขแรกที่ตรง หรือ default)",
        "Inclusive gateway (join)": "จุดรวมที่รอทุกทางที่ยังวิ่งมาได้",
        "Boundary timer": "นาฬิกาจับเวลา SLA ที่ติดขอบงาน",
        "FEEL": "ภาษาเงื่อนไขของ Camunda 8 เช่น =amount > 1000",
    }
    return guide


__all__ = ["build_workflow_guide"]
