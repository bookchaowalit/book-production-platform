"""Deterministic quality gates for document artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

from document_ir import ArtifactRef, DocumentIR
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError


@dataclass(frozen=True)
class QAResult:
    status: str
    issues: list[dict[str, str]]
    page_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _critical_texts(ir: DocumentIR) -> list[str]:
    return [
        ir.title,
        ir.customer_name,
        ir.issue_date.isoformat(),
        *[f"{name}: {value} {ir.currency}" for name, value in ir.derived_totals.items()],
    ]


def _docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    return " ".join(element.text or "" for element in root.iter() if element.tag.endswith("}t"))


def _pdf_text(path: Path) -> tuple[int, str]:
    reader = PdfReader(str(path))
    return len(reader.pages), " ".join(page.extract_text() or "" for page in reader.pages)


def qa_document(ir: DocumentIR, artifacts: list[ArtifactRef], output_dir: str | Path) -> QAResult:
    """Verify artifact integrity and normalized totals before acceptance."""

    output_path = Path(output_dir)
    issues: list[dict[str, str]] = []
    page_count: int | None = None
    critical_texts = [_normalize_text(value) for value in _critical_texts(ir)]
    for artifact in artifacts:
        path = output_path / artifact.filename
        if not path.exists():
            issues.append(_issue("artifact_missing", artifact.filename))
            continue
        if _sha256(path) != artifact.content_hash:
            issues.append(_issue("artifact_hash_mismatch", artifact.filename))
        if path.stat().st_size != artifact.byte_size:
            issues.append(_issue("artifact_size_mismatch", artifact.filename))
        if artifact.page_count is not None:
            page_count = max(page_count or 0, artifact.page_count)
            if artifact.page_count > 10:
                issues.append(_issue("page_limit_exceeded", artifact.filename))
        try:
            if artifact.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                extracted = _normalize_text(_docx_text(path))
                for expected in critical_texts:
                    if expected not in extracted:
                        issues.append(_issue("document_text_missing", f"{artifact.filename}: {expected}"))
            elif artifact.media_type == "application/pdf":
                actual_pages, raw_text = _pdf_text(path)
                if artifact.page_count != actual_pages:
                    issues.append(_issue("pdf_page_count_mismatch", artifact.filename))
                extracted = _normalize_text(raw_text)
                for expected in critical_texts:
                    if expected not in extracted:
                        issues.append(_issue("document_text_missing", f"{artifact.filename}: {expected}"))
        except (OSError, ValueError, TypeError, PdfReadError, PdfStreamError):
            issues.append(_issue("document_text_unreadable", artifact.filename))

    # Monetary totals arithmetic only applies to templates that publish
    # totals; templates with an empty derived_totals mapping (e.g.
    # transcript@1) inherit every artifact integrity gate above unchanged.
    if ir.derived_totals:
        if not {"subtotal", "tax_total", "grand_total"}.issubset(ir.derived_totals):
            issues.append(_issue("totals_missing", "derived_totals"))
        else:
            try:
                subtotal = Decimal(ir.derived_totals["subtotal"])
                row_total = sum(Decimal(row.cells[-1]) for section in ir.sections for table in section.tables for row in table.rows)
                if row_total != subtotal:
                    issues.append(_issue("subtotal_mismatch", "derived_totals.subtotal"))
                if Decimal(ir.derived_totals["grand_total"]) != subtotal + Decimal(ir.derived_totals["tax_total"]):
                    issues.append(_issue("grand_total_mismatch", "derived_totals.grand_total"))
            except (InvalidOperation, IndexError):
                issues.append(_issue("totals_not_numeric", "derived_totals"))

    return QAResult(status="passed" if not issues else "failed", issues=issues, page_count=page_count)
