from __future__ import annotations

import pytest
from workflow_ir import WorkflowIR, WorkflowRequest, compile_workflow, validate_request


def _workflow(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "workflow_id": "onboarding-01",
        "name": "Customer onboarding",
        "fields": [
            {"field_id": "customer_name", "label": "Customer name", "field_type": "text", "required": True},
            {"field_id": "seats", "label": "Seats", "field_type": "integer", "required": True},
        ],
        "form_values": {"customer_name": "Ada *Team*", "seats": 3},
        "steps": [
            {"step_id": "collect", "kind": "collect_form", "depends_on": [], "retry": {"max_attempts": 1}},
            {"step_id": "validate", "kind": "validate", "depends_on": ["collect"], "retry": {"max_attempts": 2}},
            {"step_id": "review", "kind": "human_review", "depends_on": ["validate"], "role": "ops", "retry": {"max_attempts": 1}},
            {"step_id": "render", "kind": "render_document", "depends_on": ["review"], "retry": {"max_attempts": 1}},
        ],
        "document": {
            "title": "Onboarding summary",
            "sections": [
                {"kind": "heading", "text": "Customer"},
                {"kind": "field", "field_id": "customer_name"},
                {"kind": "field", "field_id": "seats"},
            ],
        },
        "permissions": {"allowed_roles": ["ops"], "approver_role": "ops"},
    }
    payload.update(overrides)
    return payload


def test_valid_workflow_compiles_topologically_and_is_plan_only() -> None:
    request = WorkflowRequest.model_validate(_workflow())

    issues = validate_request(request)
    ir = compile_workflow(request)

    assert issues == []
    assert isinstance(ir, WorkflowIR)
    assert [step.step_id for step in ir.steps] == ["collect", "validate", "review", "render"]
    assert ir.external_effects_enabled is False
    assert ir.model_calls == 0


@pytest.mark.parametrize(
    ("steps", "code"),
    [
        (_workflow()["steps"] + [{"step_id": "render", "kind": "validate", "depends_on": []}], "duplicate_step_id"),
        ([{**step, "depends_on": ["missing"]} if step["step_id"] == "validate" else step for step in _workflow()["steps"]], "dependency_missing"),
        ([
            {"step_id": "collect", "kind": "collect_form", "depends_on": ["render"]},
            {"step_id": "validate", "kind": "validate", "depends_on": ["collect"]},
            {"step_id": "review", "kind": "human_review", "depends_on": ["validate"], "role": "ops"},
            {"step_id": "render", "kind": "render_document", "depends_on": ["review"]},
        ], "workflow_cycle"),
    ],
)
def test_invalid_dag_returns_stable_issue_code(steps: object, code: str) -> None:
    issues = validate_request(_workflow(steps=steps))

    assert code in {issue.code for issue in issues}


def test_missing_required_value_returns_needs_input() -> None:
    payload = _workflow(form_values={"customer_name": "Ada"})

    issues = validate_request(payload)

    assert any(issue.code == "needs_input" and "seats" in issue.path for issue in issues)


def test_wrong_form_value_type_is_rejected() -> None:
    payload = _workflow(form_values={"customer_name": "Ada", "seats": "three"})

    issues = validate_request(payload)

    assert any(issue.code == "field_type_invalid" for issue in issues)


def test_retry_cap_and_permission_are_fail_closed() -> None:
    steps = [dict(step) for step in _workflow()["steps"]]
    steps[1] = {**steps[1], "retry": {"max_attempts": 4}}
    steps[2] = {**steps[2], "role": "unknown"}

    issues = validate_request(_workflow(steps=steps))

    codes = {issue.code for issue in issues}
    assert "retry_cap_exceeded" in codes
    assert "role_not_allowed" in codes


def test_document_field_must_exist_in_form_schema() -> None:
    payload = _workflow(document={"title": "Doc", "sections": [{"kind": "field", "field_id": "missing"}]})

    issues = validate_request(payload)

    assert any(issue.code == "document_field_missing" for issue in issues)
