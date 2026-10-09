"""Bounded, provider-neutral extraction for Documents V1B."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

MAX_INPUT_TOKENS = 8_000
MAX_OUTPUT_TOKENS = 2_000
MAX_LINE_MAGNITUDE = Decimal("1e12")
MAX_TAX_RATE = Decimal(100)
REQUIRED_FIELDS = ("title", "customer_name", "issue_date", "currency", "line_items")
ALLOWED_FIELDS = frozenset((*REQUIRED_FIELDS, "notes"))
BRIEF_ADAPTER_VERSION = "brief-adapter.v1"

ExtractionStatus = Literal["complete", "needs_input", "rejected"]
ExtractionIssue = dict[str, str]


class BriefRequest(BaseModel):
    """Private brief envelope; the brief itself is never placed in telemetry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = "document-brief.v1"
    document_id: str
    brief: str = Field(min_length=1)
    locale: str = "th-TH"
    template_id: str = "invoice"
    template_version: str = "1"


class ExtractionResult(BaseModel):
    """Model-neutral fields and provenance returned by an extraction adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "document-extraction.v1"
    adapter_version: str = BRIEF_ADAPTER_VERSION
    status: ExtractionStatus
    fields: dict[str, Any] = Field(default_factory=dict)
    provided: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    issues: list[ExtractionIssue] = Field(default_factory=list)
    model_calls: int = 0
    adapter: str = "rule-based"
    input_tokens: int = 0
    output_tokens: int = 0


class ExtractionAdapter(Protocol):
    """Provider-neutral ``brief-adapter.v1`` boundary for a gateway-backed extractor.

    Input is the raw brief text plus the document template context and a locale
    hint. Implementations return typed name/value fields only; they never
    calculate document totals and never receive or return the raw brief in
    telemetry.
    """

    def extract(
        self,
        brief: str,
        *,
        locale: str,
        template_id: str = "invoice",
        template_version: str = "1",
        budget: Any | None = None,
    ) -> ExtractionResult:
        """Return typed proposals without calculating document totals."""


def _issue(code: str, path: str, message: str) -> ExtractionIssue:
    return {"code": code, "path": path, "message": message}


def _token_count(value: str) -> int:
    return len(re.findall(r"\S+", value))


def _decimal_text(value: str, *, preserve_scale: bool = False) -> str:
    cleaned = value.strip().replace(",", "").replace("%", "")
    parsed = Decimal(cleaned)
    if not parsed.is_finite():
        raise InvalidOperation
    normalized = format(parsed if preserve_scale else parsed.normalize(), "f")
    return "0" if normalized in {"-0", "-0.0"} else normalized


def _date_text(value: str) -> str:
    candidate = value.strip()
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        pass
    for separator in ("/", "-"):
        parts = candidate.split(separator)
        if len(parts) != 3:
            continue
        try:
            day, month, year = (int(part) for part in parts)
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    raise ValueError("date must use YYYY-MM-DD or DD/MM/YYYY")


def _currency_text(value: str) -> str:
    normalized = value.strip().upper()
    aliases = {"บาท": "THB", "฿": "THB", "ดอลลาร์": "USD", "ยูโร": "EUR"}
    candidate = aliases.get(normalized, normalized)
    if not re.fullmatch(r"[A-Z]{3}", candidate):
        raise ValueError("currency must be one three-letter code, not an ambiguous or mixed value")
    return candidate


_PATH_LIKE = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)|^[A-Za-z]:[/\\]|^/")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_SQL_FRAGMENT = re.compile(
    r"\b(?:union\s+select|drop\s+table|insert\s+into|delete\s+from|select\s+\*|or\s+1\s*=\s*1)\b",
    re.IGNORECASE,
)


def _suspicious_text(value: str) -> bool:
    """Detect path-like, HTML, or SQL-statement payloads in extracted text."""

    return bool(_PATH_LIKE.search(value) or _HTML_TAG.search(value) or _SQL_FRAGMENT.search(value))


def _bounded_number(value: str, *, upper: Decimal = MAX_LINE_MAGNITUDE, preserve_scale: bool = False) -> str:
    text = _decimal_text(value, preserve_scale=preserve_scale)
    number = Decimal(text)
    if number < 0 or number > upper:
        raise ValueError("number is outside the bounded range for a document brief")
    return text


def _label_key(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    labels = {
        "title": "title",
        "หัวข้อ": "title",
        "ชื่อเอกสาร": "title",
        "customer": "customer_name",
        "customer_name": "customer_name",
        "ลูกค้า": "customer_name",
        "date": "issue_date",
        "issue_date": "issue_date",
        "วันที่": "issue_date",
        "currency": "currency",
        "สกุลเงิน": "currency",
        "item": "line_items",
        "items": "line_items",
        "รายการ": "line_items",
        "note": "notes",
        "notes": "notes",
        "หมายเหตุ": "notes",
    }
    return labels.get(normalized, "")


def _parse_item(value: str, index: int) -> tuple[dict[str, str] | None, ExtractionIssue | None]:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) not in {3, 4} or not parts[0]:
        return None, _issue("line_item_invalid", f"line_items[{index}]", "use description | quantity | unit_price | tax_rate")
    tax_rate = parts[3] if len(parts) == 4 else "0"
    try:
        if _suspicious_text(parts[0]):
            raise ValueError("description contains a path-like, HTML, or SQL payload")
        return {
            "description": parts[0],
            "quantity": _bounded_number(parts[1]),
            "unit_price": _bounded_number(parts[2], preserve_scale=True),
            "tax_rate": _bounded_number(tax_rate, upper=MAX_TAX_RATE),
        }, None
    except (InvalidOperation, ValueError):
        return None, _issue("line_item_invalid", f"line_items[{index}]", "quantity, unit_price, and tax_rate must be bounded numbers")


def validate_extraction_result(result: ExtractionResult) -> list[ExtractionIssue]:
    """Validate adapter output before a proposal can become a document request."""

    issues: list[ExtractionIssue] = []
    unknown = sorted(set(result.fields) - ALLOWED_FIELDS)
    for field in unknown:
        issues.append(_issue("unknown_extraction_field", f"fields.{field}", "field is not allowed in document extraction"))
    if set(result.provided) & set(result.inferred):
        issues.append(_issue("duplicate_field_provenance", "provenance", "a field cannot be both provided and inferred"))
    provenance = set(result.provided) | set(result.inferred)
    for field in sorted(set(result.fields) - provenance):
        issues.append(_issue("missing_field_provenance", f"provenance.{field}", "every extracted field needs provided or inferred provenance"))
    for field in (*result.provided, *result.inferred):
        if field not in ALLOWED_FIELDS:
            issues.append(_issue("unknown_provenance_field", f"provenance.{field}", "field is not allowed in document extraction"))
        elif field not in result.fields:
            issues.append(_issue("provenance_without_value", f"provenance.{field}", "provenance requires a field value"))
    if result.adapter_version != BRIEF_ADAPTER_VERSION:
        issues.append(_issue("adapter_version_unsupported", "adapter_version", f"only {BRIEF_ADAPTER_VERSION} results are accepted"))
    if result.model_calls < 0:
        issues.append(_issue("model_calls_invalid", "model_calls", "model_calls cannot be negative"))
    if result.model_calls > 2:
        issues.append(_issue("model_call_limit_exceeded", "model_calls", "a V1B job may use at most 2 model calls"))
    if result.input_tokens > MAX_INPUT_TOKENS:
        issues.append(_issue("input_token_limit_exceeded", "input_tokens", "brief extraction input cannot exceed 8000 tokens"))
    if result.output_tokens > MAX_OUTPUT_TOKENS:
        issues.append(_issue("output_token_limit_exceeded", "output_tokens", "extraction output cannot exceed 2000 tokens"))
    if "derived_totals" in result.fields or "subtotal" in result.fields or "grand_total" in result.fields:
        issues.append(_issue("derived_total_forbidden", "fields", "totals are calculated by the recipe, not extracted"))
    return issues


class RuleBasedExtractor:
    """Parse an explicit labeled brief without invoking an LLM or external service."""

    name = "rule-based"

    def extract(
        self,
        brief: str,
        *,
        locale: str,
        template_id: str = "invoice",
        template_version: str = "1",
        budget: Any | None = None,
    ) -> ExtractionResult:
        """Parse an explicit labeled brief without invoking an LLM or external service."""

        del locale, template_id, template_version, budget
        input_tokens = _token_count(brief)
        if input_tokens > MAX_INPUT_TOKENS:
            return ExtractionResult(
                status="rejected",
                adapter=self.name,
                input_tokens=input_tokens,
                issues=[_issue("input_token_limit_exceeded", "brief", "brief extraction input cannot exceed 8000 tokens")],
            )

        fields: dict[str, Any] = {}
        provided: list[str] = []
        issues: list[ExtractionIssue] = []
        line_items: list[dict[str, str]] = []
        item_seen = False
        for raw_line in brief.splitlines():
            if not raw_line.strip() or ":" not in raw_line and "：" not in raw_line:
                continue
            label, value = re.split(r"[:：]", raw_line, maxsplit=1)
            field = _label_key(label)
            value = value.strip()
            if not field:
                continue
            if field == "line_items":
                item_seen = True
                item, issue = _parse_item(value, len(line_items))
                if issue is not None:
                    issues.append(issue)
                elif item is not None:
                    line_items.append(item)
                continue
            if field in fields:
                issues.append(_issue("duplicate_field", field, "field appears more than once"))
                continue
            try:
                if field == "issue_date":
                    fields[field] = _date_text(value)
                elif field == "currency":
                    fields[field] = _currency_text(value)
                else:
                    if _suspicious_text(value):
                        raise ValueError("value contains a path-like, HTML, or SQL payload")
                    fields[field] = value
                provided.append(field)
            except (ValueError, InvalidOperation):
                issues.append(_issue("field_invalid", field, f"could not parse {field}"))

        if item_seen and line_items:
            fields["line_items"] = line_items
            provided.append("line_items")
        provided = [field for field in (*REQUIRED_FIELDS, "notes") if field in provided]
        missing = [field for field in REQUIRED_FIELDS if field not in fields]
        if issues and any(issue["code"] in {"line_item_invalid", "field_invalid", "duplicate_field"} for issue in issues):
            status: ExtractionStatus = "needs_input"
        elif missing:
            status = "needs_input"
        else:
            status = "complete"
        result = ExtractionResult(
            status=status,
            fields=fields,
            provided=provided,
            inferred=[],
            missing=missing,
            issues=issues,
            adapter=self.name,
            input_tokens=input_tokens,
            output_tokens=_token_count(" ".join(str(value) for value in fields.values())),
        )
        validation_issues = validate_extraction_result(result)
        if validation_issues:
            result.issues.extend(validation_issues)
            result.status = "rejected"
        return result
