"""Deterministic BPMN 2.0 XML export (with diagram layout) for Workflow V1A.

The same :class:`BpmnModel` drives the ``.bpmn`` artifact and the manual
guide, so a person drawing the process in Camunda Modeler places the same
elements (same IDs, lanes, columns, flows, conditions, timers) as the file
the engine wrote.

Mapping from the IR:

* one pool (participant) named after the workflow; one lane per role in
  first-use order (steps without a role go to the ``system`` lane);
* ``collect_form`` / ``human_review`` → ``userTask``; ``validate`` /
  ``render_document`` → ``serviceTask`` (``zeebe:taskDefinition`` carries the
  retry count); ``decision`` → ``exclusiveGateway`` with FEEL conditions on its
  outgoing flows and a default flow;
* a step with several dependencies gets an ``inclusiveGateway`` join (waits for
  every input that can still arrive — the engine's "skip what was routed
  around" rule);
* ``timeout_minutes`` → boundary timer ``PT<n>M``: non-interrupting with an
  escalation user task when ``escalate_to`` is set, otherwise interrupting to a
  "Timed out" end event.

The process is exported with ``isExecutable="false"`` because V1A is
plan-only; nothing is deployed or run.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from workflow_ir import BranchCondition, WorkflowIR

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "zeebe": "http://camunda.org/schema/zeebe/1.0",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
SYSTEM_LANE = "system"
KIND_LABELS = {
    "collect_form": "Collect form",
    "validate": "Validate",
    "human_review": "Review",
    "render_document": "Render document",
    "decision": "Decision",
}
KIND_TYPES = {
    "collect_form": "userTask",
    "validate": "serviceTask",
    "human_review": "userTask",
    "render_document": "serviceTask",
    "decision": "exclusiveGateway",
}
SIZES = {"task": (100, 80), "gateway": (50, 50), "event": (36, 36)}
POOL_X, POOL_Y, LANE_LABEL, COLUMN_WIDTH, ROW_HEIGHT, FIRST_COLUMN_X = 120, 80, 30, 180, 130, 190
FEEL_OPERATORS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


@dataclass
class BpmnLane:
    id: str
    name: str
    top: int = 0
    height: int = ROW_HEIGHT


@dataclass
class BpmnNode:
    id: str
    type: str
    name: str
    lane: int
    column: int
    row: int = 0
    step_id: str | None = None
    attached_to: str | None = None
    timer: str | None = None
    interrupting: bool = True
    retries: int | None = None
    task_type: str | None = None
    candidate_group: str | None = None
    default_flow: str | None = None
    documentation: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def shape(self) -> str:
        if self.type.endswith("Gateway"):
            return "gateway"
        if self.type.endswith("Event"):
            return "event"
        return "task"

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass
class BpmnFlow:
    id: str
    source: str
    target: str
    condition: str | None = None
    is_default: bool = False
    name: str = ""
    waypoints: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class BpmnModel:
    process_id: str
    name: str
    lanes: list[BpmnLane]
    nodes: list[BpmnNode]
    flows: list[BpmnFlow]
    pool: tuple[int, int, int, int] = (0, 0, 0, 0)

    def node(self, node_id: str) -> BpmnNode:
        return next(node for node in self.nodes if node.id == node_id)


def _ncname(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-")[:60] or "workflow"


def feel_literal(field_type: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if field_type == "date":
        return f'date("{value}")'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def feel_condition(branch: BranchCondition, field_type: str) -> str:
    """Camunda 8 (FEEL) condition expression, e.g. ``=amount > 1000``."""

    return f"={branch.field_id} {FEEL_OPERATORS[branch.operator]} {feel_literal(field_type, branch.value)}"


def step_name(step_id: str, kind: str) -> str:
    return f"{KIND_LABELS[kind]} ({step_id})"


def build_bpmn_model(ir: WorkflowIR) -> BpmnModel:
    """Lay the IR out as a BPMN pool: lanes per role, columns by dependency depth."""

    lane_names: list[str] = []

    def lane_of(name: str) -> int:
        if name not in lane_names:
            lane_names.append(name)
        return lane_names.index(name)

    for step in ir.steps:
        lane_of(step.role or SYSTEM_LANE)
    for step in ir.steps:
        if step.escalate_to:
            lane_of(step.escalate_to)
    field_types = {item.field_id: item.field_type for item in ir.fields}
    by_id = {step.step_id: step for step in ir.steps}
    nodes: list[BpmnNode] = []
    flows: list[BpmnFlow] = []
    occupied: dict[tuple[int, int], int] = {}

    def add(node: BpmnNode) -> BpmnNode:
        if node.type != "boundaryEvent":  # a boundary timer sits on its task's border and takes no cell
            node.row = occupied.get((node.lane, node.column), 0)
            occupied[(node.lane, node.column)] = node.row + 1
        node.width, node.height = SIZES[node.shape]
        nodes.append(node)
        return node

    def connect(source: str, target: str, *, condition: str | None = None, is_default: bool = False, name: str = "") -> BpmnFlow:
        flow = BpmnFlow(id=f"Flow_{source}__{target}", source=source, target=target, condition=condition, is_default=is_default, name=name)
        flows.append(flow)
        return flow

    first_lane = lane_of(ir.steps[0].role or SYSTEM_LANE)
    add(BpmnNode(id="StartEvent_1", type="startEvent", name="Start", lane=first_lane, column=0))
    columns: dict[str, int] = {}
    for step in ir.steps:
        lane = lane_of(step.role or SYSTEM_LANE)
        base = max((columns[dependency] for dependency in step.depends_on), default=0)
        if len(step.depends_on) > 1:
            join = add(BpmnNode(id=f"Join_{step.step_id}", type="inclusiveGateway", name="", lane=lane, column=base + 1))
            columns[step.step_id] = base + 2
        else:
            join = None
            columns[step.step_id] = base + 1
        node = BpmnNode(
            id=step.step_id, type=KIND_TYPES[step.kind], name=step_name(step.step_id, step.kind), lane=lane,
            column=columns[step.step_id], step_id=step.step_id, retries=step.retry.max_attempts,
            documentation=f"kind={step.kind}; role={step.role or SYSTEM_LANE}; max_attempts={step.retry.max_attempts}",
        )
        if node.type == "serviceTask":
            node.task_type = step.kind
        if node.type == "userTask" and step.role:
            node.candidate_group = step.role
        add(node)
        if not step.depends_on:
            connect("StartEvent_1", step.step_id)
        for dependency in step.depends_on:
            parent = by_id[dependency]
            target = join.id if join is not None else step.step_id
            if parent.kind == "decision":
                branch = next((item for item in parent.branches if item.target == step.step_id), None)
                if branch is not None:
                    condition = feel_condition(branch, field_types.get(branch.field_id, "text"))
                    connect(dependency, target, condition=condition, name=condition.lstrip("="))
                else:
                    connect(dependency, target, is_default=True, name="default")
            else:
                connect(dependency, target)
        if join is not None:
            connect(join.id, step.step_id)
    for step in ir.steps:
        if step.kind == "decision" and step.default_target:
            node = next(item for item in nodes if item.id == step.step_id)
            node.default_flow = f"Flow_{step.step_id}__{step.default_target}"
    children = {dependency for step in ir.steps for dependency in step.depends_on}
    sinks = [step for step in ir.steps if step.step_id not in children]
    end_column = max(columns.values()) + 1
    end = add(BpmnNode(id="EndEvent_1", type="endEvent", name="Done", lane=lane_of(sinks[-1].role or SYSTEM_LANE), column=end_column))
    for step in sinks:
        connect(step.step_id, end.id)
    for step in ir.steps:
        if step.timeout_minutes is None:
            continue
        lane = lane_of(step.role or SYSTEM_LANE)
        timer = add(BpmnNode(
            id=f"Timeout_{step.step_id}", type="boundaryEvent", name=f"{step.timeout_minutes} min", lane=lane,
            column=columns[step.step_id], attached_to=step.step_id, timer=f"PT{step.timeout_minutes}M",
            interrupting=step.escalate_to is None,
        ))
        if step.escalate_to:
            escalation_lane = lane_of(step.escalate_to)
            escalate = add(BpmnNode(
                id=f"Escalate_{step.step_id}", type="userTask", name=f"Escalate ({step.step_id})", lane=escalation_lane,
                column=columns[step.step_id] + 1, candidate_group=step.escalate_to, step_id=None,
                documentation=f"SLA {step.timeout_minutes} min passed on {step.step_id}; escalate to {step.escalate_to}",
            ))
            closed = add(BpmnNode(id=f"Escalated_{step.step_id}", type="endEvent", name="Escalated", lane=escalation_lane, column=columns[step.step_id] + 2))
            connect(timer.id, escalate.id)
            connect(escalate.id, closed.id)
        else:
            closed = add(BpmnNode(id=f"TimedOut_{step.step_id}", type="endEvent", name="Timed out", lane=lane, column=columns[step.step_id] + 1))
            connect(timer.id, closed.id)
    model = BpmnModel(process_id=f"Process_{_ncname(ir.workflow_id)}", name=ir.name, lanes=[BpmnLane(id=f"Lane_{index + 1}", name=name) for index, name in enumerate(lane_names)], nodes=nodes, flows=flows)
    _layout(model)
    return model


def _layout(model: BpmnModel) -> None:
    rows = {index: 1 for index in range(len(model.lanes))}
    for node in model.nodes:
        if node.type != "boundaryEvent":
            rows[node.lane] = max(rows[node.lane], node.row + 1)
    top = POOL_Y
    for index, lane in enumerate(model.lanes):
        lane.top, lane.height = top, rows[index] * ROW_HEIGHT
        top += lane.height
    for node in model.nodes:
        if node.type == "boundaryEvent":
            continue
        center_x = FIRST_COLUMN_X + node.column * COLUMN_WIDTH + 50
        center_y = model.lanes[node.lane].top + ROW_HEIGHT // 2 + node.row * ROW_HEIGHT
        node.x, node.y = center_x - node.width // 2, center_y - node.height // 2
    for node in model.nodes:
        if node.type == "boundaryEvent" and node.attached_to:
            task = model.node(node.attached_to)
            node.x, node.y = task.x + task.width - node.width - 8, task.y + task.height - node.height // 2
    max_column = max(node.column for node in model.nodes)
    width = FIRST_COLUMN_X + (max_column + 1) * COLUMN_WIDTH + 40 - POOL_X
    model.pool = (POOL_X, POOL_Y, width, top - POOL_Y)
    for flow in model.flows:
        source, target = model.node(flow.source), model.node(flow.target)
        tx, ty = target.x, target.center[1]
        if source.type == "boundaryEvent":
            bx, by = source.center[0], source.y + source.height
            if ty > by:
                flow.waypoints = [(bx, by), (bx, ty), (tx, ty)]
            else:
                flow.waypoints = [(bx, by), (bx, by + 30), (tx - 30, by + 30), (tx - 30, ty), (tx, ty)]
            continue
        sx, sy = source.x + source.width, source.center[1]
        if sy == ty:
            flow.waypoints = [(sx, sy), (tx, ty)]
        else:
            middle = (sx + tx) // 2
            flow.waypoints = [(sx, sy), (middle, sy), (middle, ty), (tx, ty)]


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def render_bpmn(ir: WorkflowIR, model: BpmnModel | None = None) -> str:
    """Serialize the model as BPMN 2.0 XML with diagram interchange (opens in Camunda Modeler / bpmn.io)."""

    model = model or build_bpmn_model(ir)
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(_q("bpmn", "definitions"), {
        "id": "Definitions_1",
        "targetNamespace": "https://solo-empire.local/bpmn/workflow-v1a",
        "exporter": "solo-empire workflow-v1a",
        "exporterVersion": "1",
    })
    collaboration = ET.SubElement(root, _q("bpmn", "collaboration"), {"id": "Collaboration_1"})
    ET.SubElement(collaboration, _q("bpmn", "participant"), {"id": "Participant_1", "name": model.name, "processRef": model.process_id})
    process = ET.SubElement(root, _q("bpmn", "process"), {"id": model.process_id, "name": model.name, "isExecutable": "false"})
    lane_set = ET.SubElement(process, _q("bpmn", "laneSet"), {"id": "LaneSet_1"})
    for index, lane in enumerate(model.lanes):
        element = ET.SubElement(lane_set, _q("bpmn", "lane"), {"id": lane.id, "name": lane.name})
        for node in model.nodes:
            if node.lane == index:
                ET.SubElement(element, _q("bpmn", "flowNodeRef")).text = node.id
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for flow in model.flows:
        outgoing.setdefault(flow.source, []).append(flow.id)
        incoming.setdefault(flow.target, []).append(flow.id)
    for node in model.nodes:
        attributes = {"id": node.id}
        if node.name:
            attributes["name"] = node.name
        if node.type == "boundaryEvent" and node.attached_to:
            attributes["attachedToRef"] = node.attached_to
            attributes["cancelActivity"] = "true" if node.interrupting else "false"
        if node.default_flow:
            attributes["default"] = node.default_flow
        element = ET.SubElement(process, _q("bpmn", node.type), attributes)
        if node.documentation:
            ET.SubElement(element, _q("bpmn", "documentation")).text = node.documentation
        if node.task_type or node.candidate_group:
            extensions = ET.SubElement(element, _q("bpmn", "extensionElements"))
            if node.task_type:
                ET.SubElement(extensions, _q("zeebe", "taskDefinition"), {"type": node.task_type, "retries": str(node.retries or 1)})
            if node.candidate_group:
                ET.SubElement(extensions, _q("zeebe", "assignmentDefinition"), {"candidateGroups": node.candidate_group})
        for flow_id in incoming.get(node.id, []):
            ET.SubElement(element, _q("bpmn", "incoming")).text = flow_id
        for flow_id in outgoing.get(node.id, []):
            ET.SubElement(element, _q("bpmn", "outgoing")).text = flow_id
        if node.timer:
            definition = ET.SubElement(element, _q("bpmn", "timerEventDefinition"), {"id": f"TimerDefinition_{node.attached_to}"})
            ET.SubElement(definition, _q("bpmn", "timeDuration"), {_q("xsi", "type"): "bpmn:tFormalExpression"}).text = node.timer
    for flow in model.flows:
        attributes = {"id": flow.id, "sourceRef": flow.source, "targetRef": flow.target}
        if flow.name:
            attributes["name"] = flow.name
        element = ET.SubElement(process, _q("bpmn", "sequenceFlow"), attributes)
        if flow.condition:
            ET.SubElement(element, _q("bpmn", "conditionExpression"), {_q("xsi", "type"): "bpmn:tFormalExpression"}).text = flow.condition
    diagram = ET.SubElement(root, _q("bpmndi", "BPMNDiagram"), {"id": "BPMNDiagram_1"})
    plane = ET.SubElement(diagram, _q("bpmndi", "BPMNPlane"), {"id": "BPMNPlane_1", "bpmnElement": "Collaboration_1"})

    def bounds(parent: ET.Element, x: int, y: int, width: int, height: int) -> None:
        ET.SubElement(parent, _q("dc", "Bounds"), {"x": str(x), "y": str(y), "width": str(width), "height": str(height)})

    pool_x, pool_y, pool_width, pool_height = model.pool
    shape = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), {"id": "Participant_1_di", "bpmnElement": "Participant_1", "isHorizontal": "true"})
    bounds(shape, pool_x, pool_y, pool_width, pool_height)
    for lane in model.lanes:
        shape = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), {"id": f"{lane.id}_di", "bpmnElement": lane.id, "isHorizontal": "true"})
        bounds(shape, pool_x + LANE_LABEL, lane.top, pool_width - LANE_LABEL, lane.height)
    for node in model.nodes:
        attributes = {"id": f"{node.id}_di", "bpmnElement": node.id}
        if node.type == "exclusiveGateway":
            attributes["isMarkerVisible"] = "true"
        shape = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), attributes)
        bounds(shape, node.x, node.y, node.width, node.height)
    for flow in model.flows:
        edge = ET.SubElement(plane, _q("bpmndi", "BPMNEdge"), {"id": f"{flow.id}_di", "bpmnElement": flow.id})
        for x, y in flow.waypoints:
            ET.SubElement(edge, _q("di", "waypoint"), {"x": str(x), "y": str(y)})
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


__all__ = ["BpmnFlow", "BpmnLane", "BpmnModel", "BpmnNode", "build_bpmn_model", "feel_condition", "render_bpmn", "step_name"]
