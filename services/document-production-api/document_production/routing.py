"""Logic-first routing from a free-text brief into the V1A recipe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from document_ir import DocumentRequest
from pydantic import ValidationError

from .budget import BudgetExceeded, BudgetLedger
from .extraction import (
    REQUIRED_FIELDS,
    BriefRequest,
    ExtractionAdapter,
    ExtractionIssue,
    ExtractionResult,
    RuleBasedExtractor,
    validate_extraction_result,
)

RouteStatus = Literal["accepted", "needs_input", "rejected"]


@dataclass(frozen=True)
class RouteResult:
    status: RouteStatus
    request: DocumentRequest | None
    extraction: ExtractionResult
    issues: list[ExtractionIssue]


def _issue(code: str, path: str, message: str) -> ExtractionIssue:
    return {"code": code, "path": path, "message": message}


def _missing_issues(missing: list[str]) -> list[ExtractionIssue]:
    return [_issue("missing_required_field", field, f"provide {field} before rendering") for field in missing]


def _document_request(brief_request: BriefRequest, fields: dict[str, Any]) -> tuple[DocumentRequest | None, list[ExtractionIssue]]:
    payload: dict[str, Any] = {
        "schema_version": "document-request.v1",
        "template_id": brief_request.template_id,
        "template_version": brief_request.template_version,
        "document_id": brief_request.document_id,
        "locale": brief_request.locale,
        "title": fields.get("title"),
        "customer_name": fields.get("customer_name"),
        "issue_date": fields.get("issue_date"),
        "currency": fields.get("currency"),
        "line_items": fields.get("line_items", []),
    }
    if "notes" in fields:
        payload["notes"] = fields["notes"]
    try:
        return DocumentRequest.model_validate(payload), []
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            path = ".".join(str(part) for part in error["loc"]) or "$"
            issues.append(_issue("field_invalid", path, error["msg"]))
        return None, issues


def _merge_results(base: ExtractionResult, supplement: ExtractionResult) -> ExtractionResult:
    fields = {**base.fields, **supplement.fields}
    provided = [field for field in (*base.provided, *supplement.provided)]
    inferred = [field for field in (*base.inferred, *supplement.inferred)]
    provided = list(dict.fromkeys(provided))
    inferred = [field for field in dict.fromkeys(inferred) if field not in provided]
    missing = [field for field in REQUIRED_FIELDS if field not in fields]
    return ExtractionResult(
        status="complete" if not missing else "needs_input",
        fields=fields,
        provided=provided,
        inferred=inferred,
        missing=missing,
        issues=[*base.issues, *supplement.issues],
        model_calls=base.model_calls + supplement.model_calls,
        adapter=f"{base.adapter}+{supplement.adapter}",
        input_tokens=max(base.input_tokens, supplement.input_tokens),
        output_tokens=base.output_tokens + supplement.output_tokens,
    )


def route_brief(
    brief_request: BriefRequest,
    *,
    adapter: ExtractionAdapter | None = None,
    budget: BudgetLedger | None = None,
    budget_job_id: str | None = None,
) -> RouteResult:
    """Try deterministic extraction first, then an optional bounded adapter."""

    parser_result = RuleBasedExtractor().extract(brief_request.brief, locale=brief_request.locale)
    if parser_result.status == "rejected":
        return RouteResult("rejected", None, parser_result, parser_result.issues)

    extraction = parser_result
    if parser_result.status != "complete" and adapter is not None:
        if budget is None:
            issue = _issue("budget_unavailable", "budget", "an extraction budget is required before using an adapter")
            return RouteResult("rejected", None, parser_result, [*parser_result.issues, issue])
        try:
            reservation = budget.reserve(
                budget_job_id or brief_request.document_id,
                calls=1,
                input_tokens=parser_result.input_tokens,
                output_tokens=2_000,
            )
        except BudgetExceeded as exc:
            issue = _issue("budget_exceeded", "budget", str(exc))
            return RouteResult("rejected", None, parser_result, [*parser_result.issues, issue])
        supplement = adapter.extract(
            brief_request.brief,
            locale=brief_request.locale,
            template_id=brief_request.template_id,
            template_version=brief_request.template_version,
            budget=reservation,
        )
        if supplement.status == "rejected":
            return RouteResult("rejected", None, supplement, supplement.issues)
        adapter_issues = validate_extraction_result(supplement)
        if adapter_issues:
            return RouteResult("rejected", None, supplement, [*supplement.issues, *adapter_issues])
        extraction = _merge_results(parser_result, supplement)

    validation_issues = validate_extraction_result(extraction)
    if validation_issues:
        return RouteResult("rejected", None, extraction, [*extraction.issues, *validation_issues])
    if extraction.missing or extraction.status == "needs_input" or extraction.issues:
        return RouteResult("needs_input", None, extraction, [*extraction.issues, *_missing_issues(extraction.missing)])
    request, request_issues = _document_request(brief_request, extraction.fields)
    if request_issues:
        return RouteResult("needs_input", None, extraction, [*extraction.issues, *request_issues])
    return RouteResult("accepted", request, extraction, extraction.issues)
