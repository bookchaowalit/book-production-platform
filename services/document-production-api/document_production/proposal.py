"""Deterministic ``proposal@1`` recipe: a client proposal with scope, timeline and priced items.

Only the pricing section is a table, because document QA re-derives the
subtotal from the last column of every table; objective, scope,
deliverables, timeline and terms are rendered as numbered paragraphs.
Pricing uses the invoice arithmetic (Decimal half-up, per-line tax).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from document_ir import (
    DocumentIR,
    DocumentRow,
    DocumentSection,
    DocumentTable,
    LineItemInput,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .recipe import _money, _money_text

PROPOSAL_RECIPE_ID = "proposal-v1"
PROPOSAL_REQUEST_SCHEMA_VERSION = "proposal-request.v1"
SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_REQUEST_SCHEMA_PATH = SERVICE_ROOT / "schemas" / "proposal-request.v1.schema.json"

_THAI = {
    "objective": "วัตถุประสงค์", "scope": "ขอบเขตงาน", "deliverables": "สิ่งที่ส่งมอบ", "timeline": "ระยะเวลาดำเนินงาน",
    "pricing": "ค่าใช้จ่าย", "terms": "เงื่อนไข", "valid": "ข้อเสนอนี้มีผลถึงวันที่ {date}",
    "columns": ["รายการ", "จำนวน", "ราคาต่อหน่วย", "ภาษี %", "จำนวนเงิน"],
}
_ENGLISH = {
    "objective": "Objective", "scope": "Scope", "deliverables": "Deliverables", "timeline": "Timeline",
    "pricing": "Pricing", "terms": "Terms", "valid": "This proposal is valid until {date}",
    "columns": ["Item", "Qty", "Unit price", "Tax %", "Amount"],
}


class Deliverable(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)


class Milestone(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    phase: str = Field(min_length=1, max_length=160)
    duration: str = Field(min_length=1, max_length=80)


class ProposalRequest(BaseModel):
    """Structured client proposal (no model calls)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["proposal-request.v1"]
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    currency: str = Field(min_length=1, max_length=12)
    customer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    valid_until: date | None = None
    objective: list[str] = Field(default_factory=list, max_length=20)
    scope: list[str] = Field(min_length=1, max_length=40)
    deliverables: list[Deliverable] = Field(default_factory=list, max_length=40)
    timeline: list[Milestone] = Field(default_factory=list, max_length=20)
    line_items: list[LineItemInput] = Field(min_length=1, max_length=100)
    terms: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _valid_after_issue(self) -> ProposalRequest:
        if self.valid_until is not None and self.valid_until < self.issue_date:
            raise ValueError("valid_until must not be before issue_date")
        return self


def _numbered(items: list[str]) -> list[str]:
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def build_proposal_ir(request: Mapping[str, Any] | ProposalRequest) -> DocumentIR:
    parsed = request if isinstance(request, ProposalRequest) else ProposalRequest.model_validate(request)
    labels = _THAI if parsed.locale.lower().startswith("th") else _ENGLISH
    rows: list[DocumentRow] = []
    subtotal = Decimal(0)
    tax_total = Decimal(0)
    for item in parsed.line_items:
        line_subtotal = _money(item.quantity * item.unit_price)
        line_tax = _money(line_subtotal * item.tax_rate / Decimal(100))
        subtotal += line_subtotal
        tax_total += line_tax
        rows.append(DocumentRow(cells=[
            item.description, str(item.quantity), _money_text(item.unit_price), f"{item.tax_rate:g}",
            _money_text(line_subtotal),
        ]))
    subtotal, tax_total = _money(subtotal), _money(tax_total)

    sections: list[DocumentSection] = []
    if parsed.objective:
        sections.append(DocumentSection(heading=str(labels["objective"]), paragraphs=list(parsed.objective)))
    sections.append(DocumentSection(heading=str(labels["scope"]), paragraphs=_numbered(parsed.scope)))
    if parsed.deliverables:
        sections.append(DocumentSection(heading=str(labels["deliverables"]), paragraphs=_numbered(
            [f"{item.name} — {item.description}" if item.description else item.name for item in parsed.deliverables])))
    if parsed.timeline:
        sections.append(DocumentSection(heading=str(labels["timeline"]), paragraphs=_numbered(
            [f"{item.phase}: {item.duration}" for item in parsed.timeline])))
    sections.append(DocumentSection(
        heading=str(labels["pricing"]),
        tables=[DocumentTable(headers=list(labels["columns"]), rows=rows)],
    ))
    terms = list(parsed.terms)
    if parsed.valid_until is not None:
        terms.append(str(labels["valid"]).format(date=parsed.valid_until.isoformat()))
    if terms:
        sections.append(DocumentSection(heading=str(labels["terms"]), paragraphs=_numbered(terms)))

    return DocumentIR(
        document_id=parsed.document_id,
        template_ref="proposal@1",
        locale=parsed.locale,
        currency=parsed.currency,
        title=parsed.title,
        customer_name=parsed.customer_name,
        issue_date=parsed.issue_date,
        sections=sections,
        derived_totals={
            "subtotal": _money_text(subtotal),
            "tax_total": _money_text(tax_total),
            "grand_total": _money_text(_money(subtotal + tax_total)),
        },
        source_refs=[f"proposal:{parsed.document_id}"],
        assumptions={"calculation": "decimal-half-up-v1", "model_calls": "0", "recipe": PROPOSAL_RECIPE_ID},
    )


def proposal_request_schema() -> dict[str, Any]:
    schema = ProposalRequest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://solo-empire.local/schemas/document-production/proposal-request.v1.json"
    return schema


def load_proposal_request_schema() -> dict[str, Any]:
    return json.loads(PROPOSAL_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
