"""Deterministic execution-plan and Markdown document rendering for Workflow V1A."""

from __future__ import annotations

import json
import re
from typing import Any

from workflow_ir import WorkflowIR, WorkflowStep, route_workflow


def _escape_markdown(value: str) -> str:
    """Escape Markdown control characters and keep values on one line."""

    normalized = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_[\]{}#<>])", r"\\\1", normalized)


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan_step(step: WorkflowStep, selected: dict[str, str], skipped: frozenset[str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "step_id": step.step_id,
        "kind": step.kind,
        "depends_on": list(step.depends_on),
        "role": step.role,
        "max_attempts": step.retry.max_attempts,
        "status": "skipped" if step.step_id in skipped else "planned",
    }
    if step.kind == "decision":
        item["branches"] = [branch.model_dump(mode="json") for branch in step.branches]
        item["default_target"] = step.default_target
        item["selected_target"] = selected.get(step.step_id)
    if step.timeout_minutes is not None:
        item["timeout_minutes"] = step.timeout_minutes
        item["escalate_to"] = step.escalate_to
    return item


def build_execution_plan(ir: WorkflowIR) -> dict[str, Any]:
    """Build a stable plan-only representation without executing a tool.

    Decisions are evaluated against the form values (nothing runs); steps on a
    path that was not taken are ``skipped``.
    """

    route = route_workflow(ir)
    return {
        "schema_version": "workflow-plan.v1",
        "workflow_id": ir.workflow_id,
        "name": ir.name,
        "execution_mode": ir.execution_mode,
        "external_effects_enabled": ir.external_effects_enabled,
        "model_calls": ir.model_calls,
        "status": "planned",
        "steps": [_plan_step(step, route.selected, route.skipped) for step in ir.steps],
        "permissions": {
            "allowed_roles": list(ir.permissions.allowed_roles),
            "approver_role": ir.permissions.approver_role,
        },
    }


def render_execution_plan(ir: WorkflowIR) -> str:
    """Serialize the plan with stable formatting for hashing and review."""

    return json.dumps(build_execution_plan(ir), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render_document(ir: WorkflowIR) -> str:
    """Render the validated form into a reviewable Markdown document."""

    field_by_id = {field.field_id: field for field in ir.fields}
    lines = [f"# {_escape_markdown(ir.document.title)}"]
    for section in ir.document.sections:
        if section.kind == "heading":
            lines.append(f"\n## {_escape_markdown(section.text or '')}")
        elif section.kind == "paragraph":
            lines.append(f"\n{_escape_markdown(section.text or '')}")
        else:
            field = field_by_id[section.field_id or ""]
            value = _display_value(ir.form_values[section.field_id]) if section.field_id in ir.form_values else "—"
            lines.append(f"\n- **{_escape_markdown(field.label)}:** {_escape_markdown(value)}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["build_execution_plan", "render_document", "render_execution_plan"]
