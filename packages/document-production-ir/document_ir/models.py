"""Pydantic models for the deterministic Documents V1A contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

# The published request contracts advertise decimals as a JSON number or a
# plain decimal string. Pin that shape explicitly instead of inheriting
# whatever pydantic emits for ``Decimal`` (pydantic 2.14 dropped the string
# ``pattern``), so the committed schemas stay stable across pydantic releases.
DECIMAL_STRING_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"
DecimalInput = Annotated[
    Decimal,
    WithJsonSchema(
        {"anyOf": [{"type": "number"}, {"type": "string", "pattern": DECIMAL_STRING_PATTERN}]},
        mode="validation",
    ),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LineItemInput(ContractModel):
    description: str
    quantity: DecimalInput
    unit_price: DecimalInput
    tax_rate: DecimalInput = Decimal(0)

    @field_validator("quantity", "unit_price", "tax_rate", mode="before")
    @classmethod
    def parse_decimal(cls, value: Any) -> Decimal:
        return Decimal(str(value))


class DocumentRequest(ContractModel):
    schema_version: str = "document-request.v1"
    template_id: str
    template_version: str
    document_id: str
    locale: str = "th-TH"
    currency: str
    title: str
    customer_name: str
    issue_date: date
    line_items: list[LineItemInput] = Field(default_factory=list)
    notes: str | None = None


class DocumentRow(ContractModel):
    cells: list[str]


class DocumentTable(ContractModel):
    headers: list[str]
    rows: list[DocumentRow]


class DocumentImage(ContractModel):
    """A verified local raster image embedded in a section (charts in report@1)."""

    path: str
    media_type: Literal["image/png", "image/jpeg"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str | None = None


class DocumentSection(ContractModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    images: list[DocumentImage] = Field(default_factory=list)


class DocumentIR(ContractModel):
    schema_version: str = "document-ir.v1"
    document_id: str
    template_ref: str
    locale: str
    currency: str
    title: str
    customer_name: str
    issue_date: date
    sections: list[DocumentSection]
    derived_totals: dict[str, str]
    source_refs: list[str] = Field(default_factory=list)
    assumptions: dict[str, str] = Field(default_factory=dict)


class ArtifactRef(ContractModel):
    filename: str
    media_type: str
    content_hash: str
    byte_size: int
    page_count: int | None = None


class ValidationIssue(ContractModel):
    code: str
    path: str
    message: str


class ScanImportRequest(ContractModel):
    """Versioned request for local, evidence-backed field proposals.

    Exactly one source path must be set. ``source_pdf`` is the original
    PDF-only field and is kept for backward compatibility; ``source_file``
    accepts a PDF, DOCX, or raster image (PNG, JPEG, TIFF, WebP). Both paths
    are relative to the caller's input root.
    """

    schema_version: Literal["scan-import-request.v1"] = "scan-import-request.v1"
    document_id: str = Field(min_length=1, max_length=128)
    source_pdf: str | None = Field(default=None, min_length=1, max_length=512)
    source_file: str | None = Field(default=None, min_length=1, max_length=512)
    locale: str = Field(default="th-TH", min_length=2, max_length=35)

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> ScanImportRequest:
        if (self.source_pdf is None) == (self.source_file is None):
            raise ValueError("set exactly one of source_pdf or source_file")
        return self

    @property
    def source_field(self) -> Literal["source_pdf", "source_file"]:
        """Name of the request field that carries the source path."""

        return "source_pdf" if self.source_pdf is not None else "source_file"

    @property
    def source_path(self) -> str:
        """The relative source path from whichever source field is set."""

        return self.source_pdf if self.source_pdf is not None else (self.source_file or "")


class ScanFieldEvidence(ContractModel):
    page_number: int = Field(ge=1)
    method: Literal["embedded_text", "ocr"]
    source_excerpt: str = Field(min_length=1, max_length=500)


class ScanFieldProposal(ContractModel):
    status: Literal["proposed", "ambiguous"]
    value: Any | None = None
    candidates: list[Any] = Field(default_factory=list)
    evidence: list[ScanFieldEvidence] = Field(default_factory=list)


class ScanPageEvidence(ContractModel):
    page_number: int = Field(ge=1)
    method: Literal["embedded_text", "ocr", "none"]
    character_count: int = Field(ge=0)
    issue_code: str | None = None


class ScanImportIssue(ContractModel):
    code: str
    path: str
    message: str


class ScanImportManifest(ContractModel):
    schema_version: Literal["scan-import-manifest.v1"] = "scan-import-manifest.v1"
    source_sha256: str
    source_byte_size: int = Field(ge=0)
    page_count: int = Field(ge=0)
    ocr_pages_used: int = Field(ge=0)
    local_compute_cost: Literal["unknown"] = "unknown"
    engine: str = "documents-scan-import.v1"


class ScanImportQA(ContractModel):
    field_provenance_complete: bool
    required_fields_complete: bool
    no_ambiguous_fields: bool
    accepted: bool


class ScanImportResult(ContractModel):
    schema_version: Literal["scan-import-result.v1"] = "scan-import-result.v1"
    document_id: str
    status: Literal["complete", "needs_input", "rejected"]
    fields: dict[str, ScanFieldProposal] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    issues: list[ScanImportIssue] = Field(default_factory=list)
    page_evidence: list[ScanPageEvidence] = Field(default_factory=list)
    manifest: ScanImportManifest | None = None
    model_calls: Literal[0] = 0
    qa: ScanImportQA
