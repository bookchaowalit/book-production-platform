"""The pinned, model-free tabular report recipe (Documents V1A/T6.3).

Renders a ``report-document-request.v1`` payload as a deterministic tabular
monthly-report document for the production pipeline: one data table, an
explicit totals block, and reference lines for chart and workbook artifacts
produced by the upstream pipeline stages (T6.3). The recipe never calls a
model and never fetches assets.

Pinned formatting rules:

- The request carries one data ``table`` whose last column is numeric; QA
  re-derives ``totals.subtotal`` as the Decimal sum of that column, so the
  invoice totals arithmetic gates are inherited unchanged.
- ``totals.grand_total`` must equal ``subtotal + tax_total``; violations are
  rejected before rendering.
- Chart and workbook artifacts are referenced by bare file name plus
  ``media_type`` and full ``sha256`` content hash; embedding binary chart
  images inside DOCX/PDF would require an image field on the shared
  ``document_ir.DocumentIR`` contract and is intentionally out of scope here.
- Section headings are Thai (``รายงาน``, ``ข้อมูลรายงาน``, ``แผนภูมิ``) for
  ``th*`` locales and English otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from document_ir import (
    DocumentImage,
    DocumentIR,
    DocumentRow,
    DocumentSection,
    DocumentTable,
    ValidationIssue,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .images import resolve_images
from .recipe import RequestValidationError

REPORT_RECIPE_ID = "report-v1"
REPORT_DOCUMENT_REQUEST_SCHEMA_VERSION = "report-document-request.v1"
SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPORT_DOCUMENT_REQUEST_SCHEMA_PATH = SERVICE_ROOT / "schemas" / "report-document-request.v1.schema.json"

_THAI_HEADINGS = ("รายงาน", "ข้อมูลรายงาน", "แผนภูมิ")
_DEFAULT_HEADINGS = ("Report", "Report data", "Charts")
_MAX_TABLE_ROWS = 1000
_MAX_SUMMARY_LINES = 40
_MAX_CHARTS = 20


class ReportArtifactRef(BaseModel):
    """Reference to one artifact produced by an upstream pipeline stage."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\:]+$")
    media_type: str = Field(min_length=3, max_length=120)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(ge=1)


class ReportTotals(BaseModel):
    """Decimal-string totals; grand_total must equal subtotal + tax_total."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subtotal: str = Field(min_length=1, max_length=80)
    tax_total: str = Field(min_length=1, max_length=80)
    grand_total: str = Field(min_length=1, max_length=80)


class ReportTableInput(BaseModel):
    """One data table; the last column must be numeric (Decimal-parseable)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    headers: list[str] = Field(min_length=2, max_length=12)
    rows: list[list[str]] = Field(min_length=1, max_length=_MAX_TABLE_ROWS)

    @model_validator(mode="after")
    def _check_shape(self) -> ReportTableInput:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("every table row must have exactly one cell per header")
        for row in self.rows:
            try:
                Decimal(row[-1])
            except InvalidOperation as exc:
                raise ValueError(f"last table cell must be numeric: {row[-1]!r}") from exc
        return self


class ReportDocumentRequest(BaseModel):
    """Structured request for the deterministic tabular report recipe."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["report-document-request.v1"]
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)
    currency: str = Field(min_length=1, max_length=12)
    customer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    summary_lines: list[str] = Field(default_factory=list, max_length=_MAX_SUMMARY_LINES)
    table: ReportTableInput
    totals: ReportTotals
    charts: list[ReportArtifactRef] = Field(default_factory=list, max_length=_MAX_CHARTS)
    workbook: ReportArtifactRef | None = None


def _headings(locale: str) -> tuple[str, str, str]:
    if locale.lower().startswith("th"):
        return _THAI_HEADINGS
    return _DEFAULT_HEADINGS


def _validate_totals(request: ReportDocumentRequest) -> None:
    """Reject totals that do not satisfy the inherited QA arithmetic."""

    issues: list[ValidationIssue] = []
    try:
        subtotal = Decimal(request.totals.subtotal)
        tax_total = Decimal(request.totals.tax_total)
        grand_total = Decimal(request.totals.grand_total)
    except InvalidOperation:
        issues.append(ValidationIssue(code="totals_not_numeric", path="totals", message="totals must be decimal strings"))
    else:
        row_total = sum((Decimal(row[-1]) for row in request.table.rows), Decimal(0))
        if row_total != subtotal:
            issues.append(
                ValidationIssue(
                    code="subtotal_mismatch",
                    path="totals.subtotal",
                    message=f"subtotal {request.totals.subtotal} does not equal the table column sum {row_total}",
                )
            )
        if grand_total != subtotal + tax_total:
            issues.append(
                ValidationIssue(
                    code="grand_total_mismatch",
                    path="totals.grand_total",
                    message="grand_total must equal subtotal + tax_total",
                )
            )
    if issues:
        raise RequestValidationError(issues)


def _artifact_lines(refs: Sequence[ReportArtifactRef], label: str) -> list[str]:
    return [f"{label} {index}: {ref.name} ({ref.media_type}, sha256:{ref.content_hash})" for index, ref in enumerate(refs, start=1)]


def resolve_chart_images(request: ReportDocumentRequest, chart_root: str | Path) -> list[DocumentImage]:
    """Verify the raster charts named in ``charts[]`` (see :func:`resolve_images`)."""

    return resolve_images(request.charts, chart_root, field="charts", caption_label="Chart")


def build_report_ir(
    request: Mapping[str, Any] | ReportDocumentRequest,
    *,
    chart_root: str | Path | None = None,
) -> DocumentIR:
    """Normalize a tabular report request into document IR without model calls.

    With ``chart_root`` the verified PNG/JPEG charts are embedded in the chart
    section; without it every chart stays a text reference (report@1 default).
    """

    parsed = request if isinstance(request, ReportDocumentRequest) else ReportDocumentRequest.model_validate(request)
    _validate_totals(parsed)
    images = resolve_chart_images(parsed, chart_root) if chart_root is not None else []
    summary_heading, data_heading, chart_heading = _headings(parsed.locale)

    summary_paragraphs = list(parsed.summary_lines)
    if parsed.workbook is not None:
        summary_paragraphs.extend(_artifact_lines([parsed.workbook], "Workbook"))

    table = DocumentTable(headers=list(parsed.table.headers), rows=[DocumentRow(cells=list(row)) for row in parsed.table.rows])
    chart_section = DocumentSection(heading=chart_heading, paragraphs=_artifact_lines(parsed.charts, "Chart"), images=images)

    return DocumentIR(
        document_id=parsed.document_id,
        template_ref="report@1",
        locale=parsed.locale,
        currency=parsed.currency,
        title=parsed.title,
        customer_name=parsed.customer_name,
        issue_date=parsed.issue_date,
        sections=[
            DocumentSection(heading=summary_heading, paragraphs=summary_paragraphs),
            DocumentSection(heading=data_heading, tables=[table]),
            chart_section,
        ],
        derived_totals={
            "subtotal": parsed.totals.subtotal,
            "tax_total": parsed.totals.tax_total,
            "grand_total": parsed.totals.grand_total,
        },
        source_refs=[f"report:{parsed.document_id}"],
        assumptions={
            "calculation": "decimal-table-sum-report-v1",
            "model_calls": "0",
            "recipe": REPORT_RECIPE_ID,
            "schema_version": REPORT_DOCUMENT_REQUEST_SCHEMA_VERSION,
            "row_count": str(len(parsed.table.rows)),
            "chart_count": str(len(parsed.charts)),
            "embedded_chart_count": str(len(images)),
        },
    )


def load_report_document_request_schema() -> dict[str, Any]:
    """Load the published ``report-document-request.v1`` JSON Schema."""

    return json.loads(REPORT_DOCUMENT_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
