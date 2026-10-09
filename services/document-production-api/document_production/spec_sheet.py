"""Deterministic ``spec-sheet@1`` recipe: a product or part specification document.

Unlike ``report@1`` there are no monetary totals: the specification table has
free-text values with an optional unit, so a sheet can list dimensions,
volume, materials and counts side by side without summing them. Preview
images are embedded only after the shared sha256/size check in
:mod:`document_production.images`. Notes (including any mandatory
disclaimer) are rendered verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from document_ir import DocumentIR, DocumentRow, DocumentSection, DocumentTable
from pydantic import BaseModel, ConfigDict, Field

from .images import resolve_images
from .report import ReportArtifactRef

SPEC_SHEET_RECIPE_ID = "spec-sheet-v1"
SPEC_SHEET_REQUEST_SCHEMA_VERSION = "spec-sheet-request.v1"
SERVICE_ROOT = Path(__file__).resolve().parents[1]
SPEC_SHEET_REQUEST_SCHEMA_PATH = SERVICE_ROOT / "schemas" / "spec-sheet-request.v1.schema.json"

_THAI_HEADINGS = ("ภาพรวม", "ข้อมูลจำเพาะ", ("รายการ", "ค่า", "หน่วย"), "ภาพประกอบ", "หมายเหตุ")
_DEFAULT_HEADINGS = ("Overview", "Specifications", ("Item", "Value", "Unit"), "Images", "Notes")


class SpecRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=40)


class SpecSheetRequest(BaseModel):
    """Structured request for a specification sheet (no totals, no model calls)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["spec-sheet-request.v1"]
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    customer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    subject: str = Field(min_length=1, max_length=200, description="part or product the sheet describes")
    summary_lines: list[str] = Field(default_factory=list, max_length=40)
    specs: list[SpecRow] = Field(min_length=1, max_length=200)
    images: list[ReportArtifactRef] = Field(default_factory=list, max_length=10)
    notes: list[str] = Field(default_factory=list, max_length=40)


def _headings(locale: str) -> tuple[str, str, tuple[str, str, str], str, str]:
    return _THAI_HEADINGS if locale.lower().startswith("th") else _DEFAULT_HEADINGS


def build_spec_sheet_ir(
    request: Mapping[str, Any] | SpecSheetRequest,
    *,
    image_root: str | Path | None = None,
) -> DocumentIR:
    parsed = request if isinstance(request, SpecSheetRequest) else SpecSheetRequest.model_validate(request)
    overview, specs_heading, columns, images_heading, notes_heading = _headings(parsed.locale)
    images = resolve_images(parsed.images, image_root, field="images", caption_label="Image") if image_root else []
    table = DocumentTable(
        headers=list(columns),
        rows=[DocumentRow(cells=[row.label, row.value, row.unit or "-"]) for row in parsed.specs],
    )
    sections = [
        DocumentSection(heading=overview, paragraphs=[parsed.subject, *parsed.summary_lines]),
        DocumentSection(heading=specs_heading, tables=[table]),
    ]
    if images:
        sections.append(DocumentSection(heading=images_heading, images=images))
    if parsed.notes:
        sections.append(DocumentSection(heading=notes_heading, paragraphs=list(parsed.notes)))
    return DocumentIR(
        document_id=parsed.document_id,
        template_ref="spec-sheet@1",
        locale=parsed.locale,
        currency="-",
        title=parsed.title,
        customer_name=parsed.customer_name,
        issue_date=parsed.issue_date,
        sections=sections,
        derived_totals={},
        source_refs=[f"spec-sheet:{parsed.document_id}"],
        assumptions={
            "model_calls": "0",
            "recipe": SPEC_SHEET_RECIPE_ID,
            "schema_version": SPEC_SHEET_REQUEST_SCHEMA_VERSION,
            "spec_count": str(len(parsed.specs)),
            "embedded_image_count": str(len(images)),
        },
    )


def spec_sheet_request_schema() -> dict[str, Any]:
    """The JSON Schema published at ``schemas/spec-sheet-request.v1.schema.json``."""

    schema = SpecSheetRequest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://solo-empire.local/schemas/document-production/spec-sheet-request.v1.json"
    return schema


def load_spec_sheet_request_schema() -> dict[str, Any]:
    return json.loads(SPEC_SHEET_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
