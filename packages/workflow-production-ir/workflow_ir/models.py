"""Pydantic models for the versioned Workflow V1A contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class FormField(ContractModel):
    field_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=160)
    field_type: Literal["text", "integer", "decimal", "date", "boolean"]
    required: bool = True


class RetryPolicy(ContractModel):
    max_attempts: int = Field(default=1, ge=1, le=5)


BranchOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]


class BranchCondition(ContractModel):
    """One outgoing path of a ``decision`` step: go to ``target`` when ``field_id <operator> value``."""

    target: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    field_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    operator: BranchOperator
    value: StrictBool | StrictInt | StrictFloat | StrictStr


class WorkflowStep(ContractModel):
    step_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["collect_form", "validate", "render_document", "human_review", "decision"]
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    role: str | None = Field(default=None, max_length=80)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    # Exclusive gateway (kind == "decision"): conditions are checked in order, the first match wins,
    # otherwise ``default_target`` is taken. Each target depends only on this decision.
    branches: list[BranchCondition] = Field(default_factory=list, max_length=6)
    default_target: str | None = Field(default=None, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    # SLA: a step still open after ``timeout_minutes`` escalates to ``escalate_to`` (human_review only)
    # or, without an escalation role, times out.
    timeout_minutes: int | None = Field(default=None, ge=1, le=43_200)
    escalate_to: str | None = Field(default=None, max_length=80)


class DocumentSection(ContractModel):
    kind: Literal["heading", "paragraph", "field"]
    text: str | None = Field(default=None, max_length=4_000)
    field_id: str | None = Field(default=None, max_length=80)


class DocumentSpec(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[DocumentSection] = Field(min_length=1, max_length=20)


class PermissionPolicy(ContractModel):
    allowed_roles: list[str] = Field(min_length=1, max_length=20)
    approver_role: str | None = None


class WorkflowRequest(ContractModel):
    schema_version: Literal["workflow-request.v1"] = "workflow-request.v1"
    workflow_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    fields: list[FormField] = Field(min_length=1, max_length=20)
    form_values: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)
    document: DocumentSpec
    permissions: PermissionPolicy


class WorkflowIR(ContractModel):
    schema_version: Literal["workflow-ir.v1"] = "workflow-ir.v1"
    workflow_id: str
    name: str
    fields: list[FormField]
    form_values: dict[str, Any]
    steps: list[WorkflowStep]
    document: DocumentSpec
    permissions: PermissionPolicy
    execution_mode: Literal["plan_only"] = "plan_only"
    external_effects_enabled: Literal[False] = False
    model_calls: Literal[0] = 0
    source_refs: list[str] = Field(default_factory=list)


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int = Field(ge=0)


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str
