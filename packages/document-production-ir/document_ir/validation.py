"""Pure validation for structured Documents V1A requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .models import DocumentRequest, ValidationIssue

SUPPORTED_CURRENCIES = frozenset({"THB", "USD", "EUR"})
MAX_LINE_ITEMS = 100


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def validate_request(request: Mapping[str, Any] | DocumentRequest) -> list[ValidationIssue]:
    """Return stable, actionable issues without mutating the supplied value."""

    issues: list[ValidationIssue] = []
    if isinstance(request, DocumentRequest):
        parsed = request
    else:
        try:
            parsed = DocumentRequest.model_validate(request)
        except ValidationError as exc:
            for error in exc.errors():
                path = ".".join(str(part) for part in error["loc"]) or "$"
                issues.append(_issue("invalid_request", path, error["msg"]))
            return issues

    if parsed.schema_version != "document-request.v1":
        issues.append(_issue("unsupported_schema_version", "schema_version", "schema_version must be document-request.v1"))
    if not parsed.template_id:
        issues.append(_issue("template_id_required", "template_id", "template_id is required"))
    if not parsed.template_version:
        issues.append(_issue("template_version_required", "template_version", "template_version is required"))
    if not parsed.document_id:
        issues.append(_issue("document_id_required", "document_id", "document_id is required"))
    if not parsed.title:
        issues.append(_issue("title_required", "title", "title is required"))
    if not parsed.customer_name:
        issues.append(_issue("customer_name_required", "customer_name", "customer_name is required"))
    if parsed.currency not in SUPPORTED_CURRENCIES:
        issues.append(_issue("unsupported_currency", "currency", "currency must be THB, USD, or EUR"))
    if len(parsed.line_items) > MAX_LINE_ITEMS:
        issues.append(_issue("line_item_limit_exceeded", "line_items", "line_items cannot exceed 100 items"))
    if not parsed.line_items:
        issues.append(_issue("line_items_required", "line_items", "at least one line item is required"))

    for index, item in enumerate(parsed.line_items):
        prefix = f"line_items[{index}]"
        if not item.description:
            issues.append(_issue("line_item_description_required", f"{prefix}.description", "description is required"))
        if item.quantity <= 0:
            issues.append(_issue("quantity_must_be_positive", f"{prefix}.quantity", "quantity must be greater than zero"))
        if item.unit_price < 0:
            issues.append(_issue("unit_price_must_not_be_negative", f"{prefix}.unit_price", "unit_price cannot be negative"))
        if item.tax_rate < 0 or item.tax_rate > 100:
            issues.append(_issue("tax_rate_out_of_range", f"{prefix}.tax_rate", "tax_rate must be between 0 and 100"))
    return issues
