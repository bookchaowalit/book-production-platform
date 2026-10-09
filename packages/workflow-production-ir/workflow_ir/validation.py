"""Validation and topological compilation for Workflow V1A."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from .models import (
    BranchCondition,
    ValidationIssue,
    WorkflowIR,
    WorkflowRequest,
    WorkflowStep,
)

ORDERED_FIELD_TYPES = frozenset(("integer", "decimal", "date"))
ORDER_OPERATORS = frozenset(("gt", "gte", "lt", "lte"))


@dataclass(frozen=True)
class WorkflowRoute:
    """Which path every decision takes for the request's form values, and which steps are skipped."""

    selected: dict[str, str]
    skipped: frozenset[str]


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def _topological(steps: list[WorkflowStep]) -> tuple[list[WorkflowStep], list[ValidationIssue]]:
    by_id = {step.step_id: step for step in steps}
    order = {step.step_id: index for index, step in enumerate(steps)}
    indegree = {step.step_id: 0 for step in steps}
    children: dict[str, list[str]] = {step.step_id: [] for step in steps}
    issues: list[ValidationIssue] = []
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in by_id:
                issues.append(_issue("dependency_missing", f"steps.{step.step_id}.depends_on", f"unknown dependency {dependency!r}"))
                continue
            indegree[step.step_id] += 1
            children[dependency].append(step.step_id)
    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    ready.sort(key=order.__getitem__)
    result: list[WorkflowStep] = []
    while ready:
        current = ready.pop(0)
        result.append(by_id[current])
        for child in sorted(children[current], key=order.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=order.__getitem__)
    if len(result) != len(steps):
        issues.append(_issue("workflow_cycle", "steps", "workflow dependencies contain a cycle"))
    return result, issues


def _value_valid(field_type: str, value: Any) -> bool:
    if field_type == "text":
        return isinstance(value, str)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "decimal":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "date":
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    return False


def _comparable(field_type: str, value: Any) -> Any:
    if field_type == "date":
        return date.fromisoformat(value)
    if field_type in ("integer", "decimal"):
        return Decimal(str(value))
    return value


def condition_matches(condition: BranchCondition, field_type: str, form_values: Mapping[str, Any]) -> bool:
    """Deterministic evaluation of one branch condition; a missing value never matches."""

    actual = form_values.get(condition.field_id)
    if actual is None:
        return False
    try:
        left, right = _comparable(field_type, actual), _comparable(field_type, condition.value)
        if condition.operator == "eq":
            return bool(left == right)
        if condition.operator == "ne":
            return bool(left != right)
        if condition.operator == "gt":
            return bool(left > right)
        if condition.operator == "gte":
            return bool(left >= right)
        if condition.operator == "lt":
            return bool(left < right)
        return bool(left <= right)
    except (TypeError, ValueError, InvalidOperation):
        return False


def route_workflow(workflow: WorkflowRequest | WorkflowIR) -> WorkflowRoute:
    """Evaluate every decision in dependency order (plan only; nothing is executed).

    A branch target that was not selected is skipped, and so is any step whose
    dependencies are all skipped (an exclusive merge still runs when one of its
    inputs ran).
    """

    ordered, issues = _topological(list(workflow.steps))
    if issues:
        raise ValueError("invalid_request:" + ",".join(issue.code for issue in issues))
    field_types = {field.field_id: field.field_type for field in workflow.fields}
    by_id = {step.step_id: step for step in ordered}
    selected: dict[str, str] = {}
    skipped: set[str] = set()
    for step in ordered:
        if len(step.depends_on) == 1 and by_id[step.depends_on[0]].kind == "decision":
            parent = step.depends_on[0]
            if parent in skipped or selected.get(parent) != step.step_id:
                skipped.add(step.step_id)
                continue
        if step.depends_on and all(dependency in skipped for dependency in step.depends_on):
            skipped.add(step.step_id)
            continue
        if step.kind == "decision":
            chosen = next(
                (branch.target for branch in step.branches if condition_matches(branch, field_types.get(branch.field_id, "text"), workflow.form_values)),
                step.default_target,
            )
            if chosen is not None:
                selected[step.step_id] = chosen
    return WorkflowRoute(selected=selected, skipped=frozenset(skipped))


def _ancestors(step_id: str, by_id: Mapping[str, WorkflowStep]) -> set[str]:
    seen: set[str] = set()
    pending = list(by_id[step_id].depends_on)
    while pending:
        current = pending.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        pending.extend(by_id[current].depends_on)
    return seen


def _decision_issues(parsed: WorkflowRequest, field_types: Mapping[str, str]) -> list[ValidationIssue]:
    """Gateway and SLA rules; run only on an acyclic graph."""

    issues: list[ValidationIssue] = []
    by_id = {step.step_id: step for step in parsed.steps}
    collect_ids = {step.step_id for step in parsed.steps if step.kind == "collect_form"}
    allowed = set(parsed.permissions.allowed_roles)
    for step in parsed.steps:
        path = f"steps.{step.step_id}"
        if step.escalate_to is not None:
            if step.kind != "human_review":
                issues.append(_issue("escalation_requires_review", f"{path}.escalate_to", "only a human_review step can escalate"))
            if step.timeout_minutes is None:
                issues.append(_issue("escalation_timeout_required", f"{path}.timeout_minutes", "escalation needs a timeout"))
            if step.escalate_to not in allowed:
                issues.append(_issue("escalation_role_not_allowed", f"{path}.escalate_to", "escalation role is not allowed by permission policy"))
        if step.kind != "decision":
            if step.branches or step.default_target is not None:
                issues.append(_issue("branches_not_allowed", f"{path}.branches", "only a decision step has branches"))
            continue
        if step.timeout_minutes is not None:
            issues.append(_issue("timeout_not_allowed", f"{path}.timeout_minutes", "a decision is evaluated instantly"))
        if not step.branches:
            issues.append(_issue("decision_branches_required", f"{path}.branches", "a decision needs at least one condition"))
        if step.default_target is None:
            issues.append(_issue("decision_default_required", f"{path}.default_target", "a decision needs a default path"))
        targets = [branch.target for branch in step.branches] + ([step.default_target] if step.default_target else [])
        if len(targets) != len(set(targets)):
            issues.append(_issue("decision_target_duplicate", f"{path}.branches", "each path must lead to a different step"))
        for target in targets:
            if target not in by_id:
                issues.append(_issue("decision_target_missing", f"{path}.branches", f"unknown branch target {target!r}"))
            elif by_id[target].depends_on != [step.step_id]:
                issues.append(_issue("branch_target_dependencies_invalid", f"steps.{target}.depends_on", "a branch target depends only on its decision"))
        for child in parsed.steps:
            if step.step_id in child.depends_on and child.step_id not in targets:
                issues.append(_issue("decision_child_not_branch", f"steps.{child.step_id}.depends_on", "a step after a decision must be one of its paths"))
        for index, branch in enumerate(step.branches):
            branch_path = f"{path}.branches.{index}"
            field_type = field_types.get(branch.field_id)
            if field_type is None:
                issues.append(_issue("decision_field_missing", f"{branch_path}.field_id", "condition field is not in form schema"))
                continue
            if branch.operator in ORDER_OPERATORS and field_type not in ORDERED_FIELD_TYPES:
                issues.append(_issue("decision_operator_invalid", f"{branch_path}.operator", f"{branch.operator} needs an integer, decimal, or date field"))
            if not _value_valid(field_type, branch.value):
                issues.append(_issue("decision_value_type_invalid", f"{branch_path}.value", f"value does not match {field_type}"))
        if not collect_ids & _ancestors(step.step_id, by_id):
            issues.append(_issue("decision_before_collect", path, "a decision must follow collect_form"))
    if not issues:
        route = route_workflow(parsed)
        for step in parsed.steps:
            if step.kind == "render_document" and step.step_id in route.skipped:
                issues.append(_issue("render_step_unreachable", f"steps.{step.step_id}", "the form values route around render_document"))
    return issues


def validate_request(request: Mapping[str, Any] | WorkflowRequest) -> list[ValidationIssue]:
    """Return stable issues without mutating the supplied workflow."""

    if isinstance(request, WorkflowRequest):
        parsed = request
    else:
        try:
            parsed = WorkflowRequest.model_validate(request)
        except ValidationError as exc:
            return [_issue("invalid_request", ".".join(str(part) for part in error["loc"]) or "$", str(error["msg"])) for error in exc.errors()]

    issues: list[ValidationIssue] = []
    if not parsed.workflow_id.strip():
        issues.append(_issue("workflow_id_required", "workflow_id", "workflow_id is required"))
    if not parsed.name.strip():
        issues.append(_issue("name_required", "name", "name is required"))

    field_ids: set[str] = set()
    field_types: dict[str, str] = {}
    for index, field in enumerate(parsed.fields):
        if not field.field_id.strip():
            issues.append(_issue("field_id_required", f"fields.{index}.field_id", "field_id is required"))
        if field.field_id in field_ids:
            issues.append(_issue("duplicate_field_id", f"fields.{index}.field_id", "field_id is repeated"))
        field_ids.add(field.field_id)
        field_types[field.field_id] = field.field_type
        if not field.label.strip():
            issues.append(_issue("field_label_required", f"fields.{index}.label", "field label is required"))
        if field.field_id not in parsed.form_values and field.required:
            issues.append(_issue("needs_input", f"form_values.{field.field_id}", "required form value is missing"))
        elif field.field_id in parsed.form_values and parsed.form_values[field.field_id] is not None and not _value_valid(field.field_type, parsed.form_values[field.field_id]):
            issues.append(_issue("field_type_invalid", f"form_values.{field.field_id}", f"value does not match {field.field_type}"))
    for key in parsed.form_values:
        if key not in field_ids:
            issues.append(_issue("unknown_form_value", f"form_values.{key}", "value has no matching field"))

    step_ids: set[str] = set()
    for index, step in enumerate(parsed.steps):
        if step.step_id in step_ids:
            issues.append(_issue("duplicate_step_id", f"steps.{index}.step_id", "step_id is repeated"))
        step_ids.add(step.step_id)
        if step.retry.max_attempts > 3:
            issues.append(_issue("retry_cap_exceeded", f"steps.{step.step_id}.retry.max_attempts", "max_attempts cannot exceed 3"))
        if step.kind == "human_review":
            if not step.role:
                issues.append(_issue("review_role_required", f"steps.{step.step_id}.role", "human review requires a role"))
            elif step.role not in parsed.permissions.allowed_roles:
                issues.append(_issue("role_not_allowed", f"steps.{step.step_id}.role", "role is not allowed by permission policy"))
    if parsed.permissions.approver_role and parsed.permissions.approver_role not in parsed.permissions.allowed_roles:
        issues.append(_issue("approver_role_not_allowed", "permissions.approver_role", "approver role must be allowed"))

    ordered, dag_issues = _topological(parsed.steps)
    issues.extend(dag_issues)
    kinds = [step.kind for step in parsed.steps]
    if kinds.count("collect_form") != 1:
        issues.append(_issue("collect_step_required", "steps", "exactly one collect_form step is required"))
    if kinds.count("render_document") != 1:
        issues.append(_issue("render_step_required", "steps", "exactly one render_document step is required"))
    if not dag_issues and "collect_form" in kinds and "render_document" in kinds:
        collect_index = next(index for index, step in enumerate(ordered) if step.kind == "collect_form")
        render_index = next(index for index, step in enumerate(ordered) if step.kind == "render_document")
        if render_index <= collect_index:
            issues.append(_issue("render_before_collect", "steps", "render_document must follow collect_form"))
    if not dag_issues and not [issue for issue in issues if issue.code.startswith(("duplicate_", "dependency_"))]:
        issues.extend(_decision_issues(parsed, field_types))

    for index, section in enumerate(parsed.document.sections):
        if section.kind in {"heading", "paragraph"} and not (section.text or "").strip():
            issues.append(_issue("document_text_required", f"document.sections.{index}.text", "section text is required"))
        if section.kind == "field":
            if not section.field_id:
                issues.append(_issue("document_field_required", f"document.sections.{index}.field_id", "field section requires field_id"))
            elif section.field_id not in field_ids:
                issues.append(_issue("document_field_missing", f"document.sections.{index}.field_id", "document field is not in form schema"))
    return issues


def compile_workflow(request: Mapping[str, Any] | WorkflowRequest) -> WorkflowIR:
    """Validate and compile a deterministic plan-only workflow IR."""

    parsed = request if isinstance(request, WorkflowRequest) else WorkflowRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise ValueError("invalid_request:" + ",".join(issue.code for issue in issues))
    ordered, dag_issues = _topological(parsed.steps)
    if dag_issues:
        raise ValueError("invalid_request:" + ",".join(issue.code for issue in dag_issues))
    return WorkflowIR(
        workflow_id=parsed.workflow_id,
        name=parsed.name,
        fields=parsed.fields,
        form_values=parsed.form_values,
        steps=ordered,
        document=parsed.document,
        permissions=parsed.permissions,
        source_refs=[parsed.workflow_id],
    )
