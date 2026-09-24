"""Pure validation for structured Graphics V1A requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .models import GraphicRequest, ValidationIssue


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def validate_request(request: Mapping[str, Any] | GraphicRequest) -> list[ValidationIssue]:
    """Return stable, actionable issues without mutating the supplied value."""

    issues: list[ValidationIssue] = []
    if isinstance(request, GraphicRequest):
        parsed = request
    else:
        try:
            parsed = GraphicRequest.model_validate(request)
        except ValidationError as exc:
            for error in exc.errors():
                path = ".".join(str(part) for part in error["loc"]) or "$"
                issues.append(_issue("invalid_request", path, error["msg"]))
            return issues

    if not parsed.request_id:
        issues.append(_issue("request_id_required", "request_id", "request_id is required"))
    if not parsed.content:
        issues.append(_issue("content_required", "content", "content must include at least one field"))
    for key, value in parsed.content.items():
        if not key.strip():
            issues.append(_issue("content_key_required", "content", "content keys cannot be blank"))
        if not value.strip():
            issues.append(_issue("content_value_required", f"content.{key}", "content values cannot be blank"))
    return issues
