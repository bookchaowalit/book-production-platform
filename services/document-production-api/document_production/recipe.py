"""The pinned, model-free invoice recipe."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from document_ir import (
    DocumentIR,
    DocumentRequest,
    DocumentRow,
    DocumentSection,
    DocumentTable,
    ValidationIssue,
    validate_request,
)

MONEY = Decimal("0.01")
RECIPE_ID = "invoice-v1"
QUOTATION_RECIPE_ID = "quotation-v1"
RECIPE_IDS = {"invoice": RECIPE_ID, "quotation": QUOTATION_RECIPE_ID}
_ITEM_LABELS = {
    "default": ("Items", ["Description", "Qty", "Unit price", "Tax %", "Line total"]),
    "quotation-th": ("รายการเสนอราคา", ["รายการ", "จำนวน", "ราคาต่อหน่วย", "ภาษี %", "จำนวนเงิน"]),
}


class RequestValidationError(ValueError):
    """Raised when a structured request cannot enter a deterministic recipe."""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        self.issues = list(issues)
        codes = ", ".join(issue.code for issue in self.issues)
        super().__init__(f"request validation failed: {codes}")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return f"{_money(value):.2f}"


def build_document_ir(request: Mapping[str, Any] | DocumentRequest) -> DocumentIR:
    """Normalize and calculate a request without model calls or side effects."""

    parsed = request if isinstance(request, DocumentRequest) else DocumentRequest.model_validate(request)
    issues = validate_request(parsed)
    if parsed.template_id not in RECIPE_IDS or parsed.template_version != "1":
        issues.append(ValidationIssue(code="unsupported_template", path="template_id", message="only invoice@1 and quotation@1 are supported"))
    if issues:
        raise RequestValidationError(issues)

    rows: list[DocumentRow] = []
    subtotal = Decimal(0)
    tax_total = Decimal(0)
    for item in parsed.line_items:
        line_subtotal = _money(item.quantity * item.unit_price)
        line_tax = _money(line_subtotal * item.tax_rate / Decimal(100))
        subtotal += line_subtotal
        tax_total += line_tax
        rows.append(DocumentRow(cells=[
            item.description,
            str(item.quantity),
            _money_text(item.unit_price),
            f"{item.tax_rate:g}",
            _money_text(line_subtotal),
        ]))

    subtotal = _money(subtotal)
    tax_total = _money(tax_total)
    grand_total = _money(subtotal + tax_total)
    paragraphs = [parsed.notes] if parsed.notes else []
    # invoice@1 keeps its original English table labels; quotation@1 uses Thai
    # labels for th-* locales.
    label_key = "quotation-th" if parsed.template_id == "quotation" and parsed.locale.lower().startswith("th") else "default"
    heading, headers = _ITEM_LABELS[label_key]
    section = DocumentSection(
        heading=heading,
        paragraphs=paragraphs,
        tables=[DocumentTable(headers=list(headers), rows=rows)],
    )
    return DocumentIR(
        document_id=parsed.document_id,
        template_ref=f"{parsed.template_id}@{parsed.template_version}",
        locale=parsed.locale,
        currency=parsed.currency,
        title=parsed.title,
        customer_name=parsed.customer_name,
        issue_date=parsed.issue_date,
        sections=[section],
        derived_totals={
            "subtotal": _money_text(subtotal),
            "tax_total": _money_text(tax_total),
            "grand_total": _money_text(grand_total),
        },
        source_refs=[f"request:{parsed.document_id}"],
        assumptions={
            "calculation": "decimal-half-up-v1",
            "model_calls": "0",
            "recipe": RECIPE_IDS[parsed.template_id],
        },
    )
