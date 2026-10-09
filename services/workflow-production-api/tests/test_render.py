from __future__ import annotations

import json

from workflow_ir import compile_workflow
from workflow_production.qa import run_workflow_qa
from workflow_production.render import (
    build_execution_plan,
    render_document,
    render_execution_plan,
)


def _workflow() -> dict:
    return {
        "workflow_id": "render-01",
        "name": "Onboarding / เอกสาร",
        "fields": [
            {"field_id": "customer_name", "label": "Customer *name*", "field_type": "text", "required": True},
            {"field_id": "seats", "label": "Seats", "field_type": "integer", "required": True},
        ],
        "form_values": {"customer_name": "Ada *Team* {{safe}}", "seats": 3},
        "steps": [
            {"step_id": "collect", "kind": "collect_form"},
            {"step_id": "validate", "kind": "validate", "depends_on": ["collect"], "retry": {"max_attempts": 2}},
            {"step_id": "render", "kind": "render_document", "depends_on": ["validate"]},
        ],
        "document": {
            "title": "Customer *Brief*",
            "sections": [
                {"kind": "heading", "text": "Details #1"},
                {"kind": "paragraph", "text": "Review [the] request."},
                {"kind": "field", "field_id": "customer_name"},
                {"kind": "field", "field_id": "seats"},
            ],
        },
        "permissions": {"allowed_roles": ["ops"]},
    }


def test_plan_is_topological_and_plan_only() -> None:
    ir = compile_workflow(_workflow())
    plan = build_execution_plan(ir)

    assert [step["step_id"] for step in plan["steps"]] == ["collect", "validate", "render"]
    assert plan["execution_mode"] == "plan_only"
    assert plan["external_effects_enabled"] is False
    assert plan["model_calls"] == 0


def test_markdown_escapes_values_and_qa_has_no_unresolved_placeholders() -> None:
    ir = compile_workflow(_workflow())
    document = render_document(ir)
    plan = render_execution_plan(ir)

    assert "Customer \\*name\\*" in document
    assert "Ada \\*Team\\* \\{\\{safe\\}\\}" in document
    assert "# Customer \\*Brief\\*" in document
    assert "{{" not in document and "}}" not in document
    assert run_workflow_qa(plan, document) == []
    assert json.loads(plan)["steps"][1]["max_attempts"] == 2
