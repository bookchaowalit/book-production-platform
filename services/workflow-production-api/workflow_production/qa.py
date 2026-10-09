"""Deterministic safety and completeness checks for Workflow V1A artifacts."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BPMNDI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
STEP_ELEMENT_TYPES = {
    "collect_form": "userTask", "validate": "serviceTask", "human_review": "userTask",
    "render_document": "serviceTask", "decision": "exclusiveGateway",
}
STEP_STATUSES = frozenset(("planned", "skipped"))
QA_CHECKS: tuple[str, ...] = (
    "plan_json", "plan_schema", "plan_only", "no_external_effects", "zero_model_calls", "steps_present",
    "step_ids_unique", "step_status", "retry_cap", "render_not_skipped", "decision_route", "document_title",
    "no_placeholders", "bpmn_parse", "bpmn_no_dtd", "bpmn_single_process", "bpmn_step_nodes", "bpmn_dependency_flows",
    "bpmn_start_end", "bpmn_flow_refs", "bpmn_timers", "bpmn_gateway_default", "bpmn_diagram_shapes",
)


def run_workflow_qa(plan_text: str, document_text: str) -> list[str]:
    """Return stable QA issue codes without executing workflow steps."""

    issues: list[str] = []
    try:
        plan = json.loads(plan_text)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        plan = None
        issues.append("execution_plan_invalid_json")
    if isinstance(plan, dict):
        if plan.get("schema_version") != "workflow-plan.v1":
            issues.append("execution_plan_schema_invalid")
        if plan.get("execution_mode") != "plan_only":
            issues.append("execution_mode_invalid")
        if plan.get("external_effects_enabled") is not False:
            issues.append("external_effects_enabled")
        if plan.get("model_calls") != 0:
            issues.append("model_calls_nonzero")
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append("execution_steps_missing")
        else:
            step_ids: list[str] = []
            for step in steps:
                if not isinstance(step, dict):
                    issues.append("execution_step_invalid")
                    continue
                step_id = step.get("step_id")
                if not isinstance(step_id, str) or not step_id:
                    issues.append("execution_step_id_invalid")
                else:
                    step_ids.append(step_id)
                if step.get("status") not in STEP_STATUSES:
                    issues.append("execution_step_status_invalid")
                if step.get("kind") == "render_document" and step.get("status") != "planned":
                    issues.append("render_step_skipped")
                if step.get("kind") == "decision":
                    targets = [item.get("target") for item in step.get("branches", []) if isinstance(item, dict)] + [step.get("default_target")]
                    if step.get("status") == "planned" and step.get("selected_target") not in targets:
                        issues.append("decision_route_invalid")
                if not isinstance(step.get("max_attempts"), int) or not 1 <= step["max_attempts"] <= 3:
                    issues.append("execution_retry_cap_invalid")
            if len(step_ids) != len(set(step_ids)):
                issues.append("execution_step_duplicate")
    if not document_text.strip():
        issues.append("document_empty")
    elif not document_text.startswith("# "):
        issues.append("document_title_missing")
    if "{{" in document_text or "}}" in document_text:
        issues.append("unresolved_placeholder")
    return sorted(set(issues))


def _paths(flows: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Step-to-step connections, looking through generated ``Join_*`` gateways."""

    direct = set(flows)
    through = {(source, target) for source, join in flows if join.startswith("Join_") for middle, target in flows if middle == join}
    return direct | through


def run_bpmn_qa(bpmn_text: str, plan_text: str) -> list[str]:
    """Check the BPMN export against the execution plan (structure, flows, timers, diagram)."""

    issues: list[str] = []
    if "<!DOCTYPE" in bpmn_text or "<!ENTITY" in bpmn_text:
        return ["bpmn_dtd_forbidden"]
    try:
        root = ET.fromstring(bpmn_text)
        plan: Any = json.loads(plan_text)
    except (ET.ParseError, json.JSONDecodeError, TypeError):
        return ["bpmn_parse_failed"]
    if root.tag != f"{BPMN}definitions" or not isinstance(plan, dict):
        return ["bpmn_root_invalid"]
    processes = root.findall(f"{BPMN}process")
    if len(processes) != 1:
        return ["bpmn_process_count_invalid"]
    process = processes[0]
    nodes = {element.get("id"): element for element in process if element.get("id") and not element.tag.endswith(("sequenceFlow", "laneSet"))}
    flows = [(flow.get("sourceRef") or "", flow.get("targetRef") or "") for flow in process.findall(f"{BPMN}sequenceFlow")]
    steps = [step for step in plan.get("steps", []) if isinstance(step, dict)]
    for step in steps:
        element = nodes.get(step.get("step_id"))
        if element is None or element.tag != f"{BPMN}{STEP_ELEMENT_TYPES.get(str(step.get('kind')), '')}":
            issues.append("bpmn_step_node_missing")
            continue
        if step.get("timeout_minutes"):
            timers = [item for item in process.findall(f"{BPMN}boundaryEvent") if item.get("attachedToRef") == step["step_id"]]
            duration = timers[0].find(f"{BPMN}timerEventDefinition/{BPMN}timeDuration") if timers else None
            if duration is None or duration.text != f"PT{step['timeout_minutes']}M":
                issues.append("bpmn_timer_mismatch")
        if step.get("kind") == "decision" and not element.get("default"):
            issues.append("bpmn_gateway_default_missing")
    paths = _paths(flows)
    for step in steps:
        for dependency in step.get("depends_on", []):
            if (dependency, step.get("step_id")) not in paths:
                issues.append("bpmn_dependency_flow_missing")
    if len(process.findall(f"{BPMN}startEvent")) != 1 or not process.findall(f"{BPMN}endEvent"):
        issues.append("bpmn_start_end_invalid")
    if any(source not in nodes or target not in nodes for source, target in flows):
        issues.append("bpmn_flow_ref_invalid")
    shapes = {shape.get("bpmnElement") for shape in root.iter(f"{BPMNDI}BPMNShape")}
    edges = {edge.get("bpmnElement") for edge in root.iter(f"{BPMNDI}BPMNEdge")}
    if not set(nodes) <= shapes:
        issues.append("bpmn_shape_missing")
    if {flow.get("id") for flow in process.findall(f"{BPMN}sequenceFlow")} - edges:
        issues.append("bpmn_edge_missing")
    return sorted(set(issues))


__all__ = ["QA_CHECKS", "run_bpmn_qa", "run_workflow_qa"]
