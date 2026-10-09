"""Deterministic, local-first scan import with auditable field proposals.

Accepted sources are PDF (embedded text, OCR fallback per low-text page), DOCX
(python-docx text, no OCR), and raster images (PNG, JPEG, TIFF, WebP; one OCR
page). Extension and magic bytes must agree; nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol

from document_ir import (
    ScanFieldEvidence,
    ScanFieldProposal,
    ScanImportIssue,
    ScanImportManifest,
    ScanImportQA,
    ScanImportRequest,
    ScanImportResult,
    ScanPageEvidence,
)
from docx import Document as load_docx
from docx.document import Document as DocxDocument
from docx.table import Table as DocxTable
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .extraction import REQUIRED_FIELDS, ExtractionResult, RuleBasedExtractor

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_OCR_PAGES_PER_DOCUMENT = 10
MIN_EMBEDDED_TEXT_CHARS = 24
MAX_PAGE_TEXT_CHARS = 25_000
MAX_RENDERED_IMAGE_BYTES = 8 * 1024 * 1024
OCR_TIMEOUT_SECONDS = 30
FIELD_EVIDENCE_CHARS = 500
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_ARCHIVE_ENTRIES = 5_000
MAX_DOCX_PAGES = 50
MAX_IMAGE_SIDE_PX = 12_000
MAX_IMAGE_PIXELS = 50_000_000
MAX_TIFF_IFD_ENTRIES = 4_096

SourceKind = Literal["pdf", "docx", "png", "jpeg", "tiff", "webp"]
IMAGE_KINDS: frozenset[str] = frozenset({"png", "jpeg", "tiff", "webp"})
_SUFFIX_KINDS: dict[str, SourceKind] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

_FIELD_LABELS = {
    "title": {"title", "หัวข้อ", "ชื่อเอกสาร"},
    "customer_name": {"customer", "customer_name", "ลูกค้า"},
    "issue_date": {"date", "issue_date", "วันที่"},
    "currency": {"currency", "สกุลเงิน"},
    "line_items": {"item", "items", "รายการ"},
    "notes": {"note", "notes", "หมายเหตุ"},
}
_LABEL_TO_FIELD = {
    label.strip().lower().replace(" ", "_"): field
    for field, labels in _FIELD_LABELS.items()
    for label in labels
}


class OCRAdapter(Protocol):
    def extract_page(self, pdf_path: Path, page_number: int, *, locale: str) -> str:
        """Return OCR text for exactly one 1-based page of a validated local PDF."""

    def extract_image(self, image_path: Path, *, locale: str) -> str:
        """Return OCR text for one validated local raster image."""


class ScanInputError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


class OCRFailure(RuntimeError):
    """Raised when bounded local rendering or OCR fails for one page."""


@dataclass(frozen=True)
class TesseractOCRAdapter:
    """Optional Poppler + Tesseract adapter with fixed commands and resource caps.

    PDF pages need both ``pdftoppm`` and ``tesseract``; images need only
    ``tesseract``. ``pdftoppm`` is ``None`` for an image-only adapter.
    """

    pdftoppm: str | None
    tesseract: str

    @classmethod
    def from_system(cls) -> TesseractOCRAdapter | None:
        """Adapter for PDF pages; ``None`` unless Poppler and Tesseract are both installed."""

        pdftoppm = shutil.which("pdftoppm")
        tesseract = shutil.which("tesseract")
        if pdftoppm is None or tesseract is None:
            return None
        return cls(pdftoppm=pdftoppm, tesseract=tesseract)

    @classmethod
    def from_system_for_images(cls) -> TesseractOCRAdapter | None:
        """Adapter for raster images; ``None`` unless Tesseract is installed (Poppler optional)."""

        tesseract = shutil.which("tesseract")
        if tesseract is None:
            return None
        return cls(pdftoppm=shutil.which("pdftoppm"), tesseract=tesseract)

    def extract_image(self, image_path: Path, *, locale: str) -> str:
        language = _ocr_language(locale)
        with tempfile.TemporaryDirectory(prefix="document-scan-") as temp_dir:
            return _recognize_image(self.tesseract, image_path, Path(temp_dir), language)

    def extract_page(self, pdf_path: Path, page_number: int, *, locale: str) -> str:
        language = _ocr_language(locale)
        if self.pdftoppm is None:
            raise OCRFailure("page rendering requires Poppler")
        with tempfile.TemporaryDirectory(prefix="document-scan-") as temp_dir:
            workdir = Path(temp_dir)
            image_prefix = workdir / "page"
            render = subprocess.run(
                [
                    self.pdftoppm,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-scale-to",
                    "2000",
                    "-png",
                    str(pdf_path),
                    str(image_prefix),
                ],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=OCR_TIMEOUT_SECONDS,
                shell=False,
                env=_ocr_environment(),
            )
            if render.returncode != 0:
                raise OCRFailure("page render failed")

            image_path = image_prefix.with_suffix(".png")
            if not image_path.is_file() or image_path.stat().st_size > MAX_RENDERED_IMAGE_BYTES:
                raise OCRFailure("rendered page exceeded the image limit")
            return _recognize_image(self.tesseract, image_path, workdir, language)


def _recognize_image(tesseract: str, image_path: Path, workdir: Path, language: str) -> str:
    """Run Tesseract once with fixed arguments, no shell, a timeout, and an output cap."""

    text_path = workdir / "recognized.txt"
    with text_path.open("wb") as output_stream:
        recognition = subprocess.run(
            [tesseract, str(image_path), "stdout", "-l", language, "--psm", "6"],
            cwd=workdir,
            stdout=output_stream,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=OCR_TIMEOUT_SECONDS,
            shell=False,
            env=_ocr_environment(),
        )
    if recognition.returncode != 0:
        raise OCRFailure("text recognition failed")
    if text_path.stat().st_size > MAX_PAGE_TEXT_CHARS * 4:
        raise OCRFailure("recognized page exceeded the text limit")
    text = text_path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_PAGE_TEXT_CHARS:
        raise OCRFailure("recognized page exceeded the text limit")
    return text


def _ocr_environment() -> dict[str, str]:
    environment = {"PATH": os.environ.get("PATH", "")}
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _ocr_language(locale: str) -> str:
    normalized = locale.lower().replace("_", "-")
    if normalized == "en" or normalized.startswith("en-"):
        return "eng"
    if normalized == "th" or normalized.startswith("th-"):
        return "tha+eng"
    raise OCRFailure("OCR locale is not supported")


def _failure_result(document_id: str, code: str, path: str, message: str) -> ScanImportResult:
    return ScanImportResult(
        document_id=document_id,
        status="rejected",
        issues=[ScanImportIssue(code=code, path=path, message=message)],
        qa=ScanImportQA(
            field_provenance_complete=False,
            required_fields_complete=False,
            no_ambiguous_fields=True,
            accepted=False,
        ),
    )


def source_kind(raw_path: str) -> SourceKind | None:
    """Return the supported source kind implied by a path's extension, if any."""

    return _SUFFIX_KINDS.get(PurePosixPath(raw_path.replace("\\", "/")).suffix.lower())


@dataclass(frozen=True)
class _LoadedSource:
    kind: SourceKind
    path: Path
    contents: bytes
    pages: list[str]
    over_limit_pages: frozenset[int]


def _validated_source(request: ScanImportRequest, input_root: str | Path) -> tuple[Path, bytes, SourceKind]:
    field = request.source_field
    is_legacy_pdf = field == "source_pdf"
    raw_path = request.source_path.strip()
    posix = PurePosixPath(raw_path.replace("\\", "/"))
    windows = PureWindowsPath(raw_path)
    invalid_message = (
        "source must be a relative PDF path inside the input root"
        if is_legacy_pdf
        else "source must be a relative file path inside the input root"
    )
    if (
        not raw_path
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ScanInputError("source_path_invalid", field, invalid_message)
    kind = source_kind(raw_path)
    if is_legacy_pdf and kind != "pdf":
        raise ScanInputError("source_path_invalid", field, invalid_message)
    if kind is None:
        raise ScanInputError(
            "source_type_unsupported",
            field,
            "supported sources are .pdf, .docx, .png, .jpg, .jpeg, .tif, .tiff, and .webp",
        )

    try:
        root = Path(input_root).resolve(strict=True)
        source = (root / raw_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ScanInputError("source_not_found", field, "source file or input root was not found") from exc
    if not root.is_dir() or not source.is_relative_to(root):
        raise ScanInputError("source_path_invalid", field, "source must stay inside the input root")
    if not source.is_file():
        if kind == "pdf":
            raise ScanInputError("source_not_pdf", field, "source must be a regular PDF file")
        raise ScanInputError("source_not_file", field, "source must be a regular file")
    if source_kind(source.name) != kind:
        if kind == "pdf":
            raise ScanInputError("source_not_pdf", field, "source must be a regular PDF file")
        raise ScanInputError("source_type_mismatch", field, "resolved source extension does not match the request")

    try:
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise ScanInputError("source_size_limit_exceeded", field, "source exceeds the 20 MiB input limit")
        with source.open("rb") as source_stream:
            contents = source_stream.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ScanInputError("source_read_failed", field, "source file could not be read") from exc
    if len(contents) > MAX_SOURCE_BYTES:
        raise ScanInputError("source_size_limit_exceeded", field, "source exceeds the 20 MiB input limit")

    detected = _sniff_source(contents)
    if kind == "docx" and detected == "ole":
        raise ScanInputError(
            "docx_encrypted_or_legacy",
            field,
            "encrypted DOCX files and legacy .doc (OLE) files are not supported",
        )
    # A PDF header may legally follow leading bytes, so unknown content stays on the PDF parser path.
    agrees = detected in {None, "pdf"} if kind == "pdf" else detected == ("zip" if kind == "docx" else kind)
    if not agrees:
        raise ScanInputError("source_type_mismatch", field, "file content does not match the file extension")
    return source, contents, kind


def _sniff_source(contents: bytes) -> str | None:
    if contents.startswith(b"%PDF-"):
        return "pdf"
    if contents.startswith(b"PK\x03\x04"):
        return "zip"
    if contents.startswith(_OLE_MAGIC):
        return "ole"
    if contents.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if contents.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if contents[:4] in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}:
        return "tiff"
    if contents[:4] == b"RIFF" and contents[8:12] == b"WEBP":
        return "webp"
    return None


def _load_source(request: ScanImportRequest, input_root: str | Path) -> _LoadedSource:
    source, contents, kind = _validated_source(request, input_root)
    field = request.source_field
    if kind == "pdf":
        try:
            reader = PdfReader(BytesIO(contents), strict=True)
            pages, over_limit = _extract_page_text(reader, field)
        except ScanInputError:
            raise
        except (OSError, PyPdfError, TypeError, ValueError, IndexError) as exc:
            raise ScanInputError("pdf_invalid", field, "source is not a supported, well-formed PDF") from exc
        return _LoadedSource(kind, source, contents, pages, frozenset(over_limit))
    if kind == "docx":
        return _LoadedSource(kind, source, contents, _extract_docx_pages(contents, field), frozenset())
    width, height = _image_dimensions(kind, contents, field)
    if width > MAX_IMAGE_SIDE_PX or height > MAX_IMAGE_SIDE_PX or width * height > MAX_IMAGE_PIXELS:
        raise ScanInputError(
            "image_too_large",
            field,
            "image may be at most 12,000 px per side and 50 megapixels",
        )
    return _LoadedSource(kind, source, contents, [""], frozenset())


def _extract_page_text(reader: PdfReader, field: str = "source_pdf") -> tuple[list[str], set[int]]:
    if reader.is_encrypted:
        raise ScanInputError("pdf_encrypted", field, "encrypted PDFs are not supported")
    page_count = len(reader.pages)
    if page_count < 1:
        raise ScanInputError("pdf_empty", field, "PDF must contain at least one page")
    if page_count > MAX_PDF_PAGES:
        raise ScanInputError("pdf_page_limit_exceeded", field, "PDF may contain at most 50 pages")
    page_text: list[str] = []
    over_limit_pages: set[int] = set()
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # Normalize parser-specific failures at the untrusted PDF boundary.
            raise ScanInputError("pdf_text_extraction_failed", f"pages[{index}]", "PDF text could not be extracted safely") from exc
        if len(text) > MAX_PAGE_TEXT_CHARS:
            page_text.append("")
            over_limit_pages.add(index)
        else:
            page_text.append(text)
    return page_text, over_limit_pages


def _extract_docx_pages(contents: bytes, field: str) -> list[str]:
    """Bound the archive before parsing, then split DOCX text into synthetic pages."""

    try:
        with zipfile.ZipFile(BytesIO(contents)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError) as exc:
        raise ScanInputError("docx_invalid", field, "source is not a well-formed DOCX file") from exc
    if len(entries) > MAX_DOCX_ARCHIVE_ENTRIES:
        raise ScanInputError("docx_uncompressed_limit_exceeded", field, "DOCX archive contains too many entries")
    if sum(entry.file_size for entry in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ScanInputError(
            "docx_uncompressed_limit_exceeded",
            field,
            "DOCX content exceeds the 100 MiB uncompressed limit",
        )
    if any(entry.flag_bits & 0x1 for entry in entries):
        raise ScanInputError("docx_encrypted_or_legacy", field, "encrypted DOCX files are not supported")
    if "word/document.xml" not in {entry.filename for entry in entries}:
        raise ScanInputError("docx_invalid", field, "source is not a well-formed DOCX file")
    try:
        document = load_docx(BytesIO(contents))
        lines = _docx_lines(document)
    except Exception as exc:  # Normalize parser-specific failures at the untrusted DOCX boundary.
        raise ScanInputError("docx_invalid", field, "DOCX text could not be extracted safely") from exc
    return _paginate_lines(lines, field)


def _docx_label_field(text: str) -> str | None:
    label = text.strip().rstrip(":：").strip().lower().replace(" ", "_")
    return _LABEL_TO_FIELD.get(label)


def _docx_table_lines(table: DocxTable) -> list[str]:
    lines: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        previous_cell: object = None
        for cell in row.cells:
            if cell._tc is previous_cell:  # horizontally merged cells repeat the same element
                continue
            previous_cell = cell._tc
            text = cell.text.strip()
            if text:
                cells.append(text)
        if not cells:
            continue
        if len(cells) >= 2 and _docx_label_field(cells[0]) is not None:
            label = cells[0].strip().rstrip(":：").strip()
            lines.append(f"{label}: " + " | ".join(" ".join(value.split()) for value in cells[1:]))
        elif any(":" in text or "：" in text for text in cells):
            for text in cells:
                lines.extend(text.splitlines())
        else:
            lines.append(" | ".join(" ".join(text.split()) for text in cells))
    return lines


def _docx_lines(document: DocxDocument) -> list[str]:
    lines: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, DocxTable):
            lines.extend(_docx_table_lines(block))
        else:
            lines.extend(block.text.splitlines())
    return lines


def _paginate_lines(lines: Sequence[str], field: str) -> list[str]:
    """Group lines into pages of at most MAX_PAGE_TEXT_CHARS, splitting only on line breaks."""

    pages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        chunks = [line[start : start + MAX_PAGE_TEXT_CHARS] for start in range(0, len(line), MAX_PAGE_TEXT_CHARS)] or [""]
        for chunk in chunks:
            added = len(chunk) + (1 if current else 0)
            if current and size + added > MAX_PAGE_TEXT_CHARS:
                pages.append("\n".join(current))
                if len(pages) >= MAX_DOCX_PAGES:
                    raise ScanInputError(
                        "docx_page_limit_exceeded",
                        field,
                        "DOCX text may fill at most 50 pages of 25,000 characters",
                    )
                current, size, added = [], 0, len(chunk)
            current.append(chunk)
            size += added
    pages.append("\n".join(current))
    return pages


def _image_dimensions(kind: str, data: bytes, field: str) -> tuple[int, int]:
    """Read width and height from the image header without decoding pixels."""

    try:
        width, height = _parse_image_header(kind, data)
    except (struct.error, IndexError, ValueError) as exc:
        raise ScanInputError("image_invalid", field, "image header could not be read") from exc
    if width < 1 or height < 1:
        raise ScanInputError("image_invalid", field, "image header could not be read")
    return width, height


def _parse_image_header(kind: str, data: bytes) -> tuple[int, int]:
    if kind == "png":
        if data[12:16] != b"IHDR":
            raise ValueError("missing IHDR")
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if kind == "jpeg":
        return _jpeg_dimensions(data)
    if kind == "tiff":
        return _tiff_dimensions(data)
    if kind == "webp":
        return _webp_dimensions(data)
    raise ValueError("not an image kind")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            raise ValueError("JPEG marker expected")
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker in {0xD9, 0xDA}:
            break
        (segment_length,) = struct.unpack(">H", data[index + 2 : index + 4])
        if segment_length < 2:
            raise ValueError("JPEG segment length")
        if marker in _JPEG_SOF_MARKERS:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        index += 2 + segment_length
    raise ValueError("JPEG frame header not found")


def _tiff_dimensions(data: bytes) -> tuple[int, int]:
    order = "<" if data[:2] == b"II" else ">"
    (magic,) = struct.unpack(order + "H", data[2:4])
    if magic != 42:
        raise ValueError("BigTIFF is not supported")
    (offset,) = struct.unpack(order + "I", data[4:8])
    (count,) = struct.unpack(order + "H", data[offset : offset + 2])
    if count > MAX_TIFF_IFD_ENTRIES:
        raise ValueError("TIFF directory too large")
    dimensions: dict[int, int] = {}
    for entry_index in range(count):
        entry = offset + 2 + 12 * entry_index
        tag, value_type, _ = struct.unpack(order + "HHI", data[entry : entry + 8])
        if tag not in {256, 257}:
            continue
        if value_type == 3:
            (value,) = struct.unpack(order + "H", data[entry + 8 : entry + 10])
        elif value_type == 4:
            (value,) = struct.unpack(order + "I", data[entry + 8 : entry + 12])
        else:
            raise ValueError("unsupported TIFF dimension type")
        dimensions[tag] = int(value)
    return dimensions[256], dimensions[257]


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if len(data) < 30:
        raise ValueError("WebP header too short")
    if chunk == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if chunk == b"VP8L":
        if data[20] != 0x2F:
            raise ValueError("VP8L signature")
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise ValueError("VP8 start code")
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    raise ValueError("unknown WebP chunk")


def _labeled_excerpts(text: str) -> dict[str, list[str]]:
    excerpts: dict[str, list[str]] = defaultdict(list)
    for line in text.splitlines():
        if ":" not in line and "：" not in line:
            continue
        normalized_line = line.replace("：", ":", 1)
        label = normalized_line.split(":", 1)[0].strip().lower().replace(" ", "_")
        field = _LABEL_TO_FIELD.get(label)
        if field is not None and line.strip():
            excerpts[field].append(line.strip()[:FIELD_EVIDENCE_CHARS])
    return excerpts


def _proposal_evidence(
    field: str,
    result: ExtractionResult,
    excerpts: dict[str, list[str]],
    *,
    page_number: int,
    method: Literal["embedded_text", "ocr"],
) -> list[ScanFieldEvidence]:
    if field not in result.fields:
        return []
    lines = excerpts.get(field, [])
    if field == "line_items":
        count = len(result.fields[field])
        lines = lines[-count:] if count else []
    return [
        ScanFieldEvidence(page_number=page_number, method=method, source_excerpt=line)
        for line in lines
    ]


def _merge_field_values(
    page_fields: Sequence[tuple[dict[str, object], dict[str, list[ScanFieldEvidence]]]],
    issues: list[ScanImportIssue],
) -> tuple[dict[str, ScanFieldProposal], list[str]]:
    scalar_values: dict[str, list[tuple[object, ScanFieldEvidence]]] = defaultdict(list)
    line_items: list[tuple[object, ScanFieldEvidence]] = []
    for fields, field_evidence in page_fields:
        for name, value in fields.items():
            evidences = field_evidence.get(name, [])
            if name == "line_items" and isinstance(value, list):
                line_items.extend(zip(value, evidences))
            elif evidences:
                scalar_values[name].extend((value, evidence) for evidence in evidences)

    proposals: dict[str, ScanFieldProposal] = {}
    ambiguous: list[str] = []
    for name, observed in scalar_values.items():
        distinct: list[object] = []
        evidence: list[ScanFieldEvidence] = []
        for value, citation in observed:
            if value not in distinct:
                distinct.append(value)
            evidence.append(citation)
        duplicate_within_page = len({item.page_number for item in evidence}) < len(evidence)
        if len(distinct) > 1 or duplicate_within_page:
            proposals[name] = ScanFieldProposal(status="ambiguous", candidates=distinct, evidence=evidence)
            ambiguous.append(name)
            issues.append(
                ScanImportIssue(
                    code="ambiguous_field",
                    path=f"fields.{name}",
                    message="source contains conflicting values; human review is required",
                )
            )
        else:
            proposals[name] = ScanFieldProposal(status="proposed", value=distinct[0], evidence=evidence)

    if line_items:
        proposals["line_items"] = ScanFieldProposal(
            status="proposed",
            value=[value for value, _ in line_items],
            evidence=[citation for _, citation in line_items],
        )
    return proposals, ambiguous


def run_scan_import(
    request: ScanImportRequest | dict[str, object],
    *,
    input_root: str | Path,
    ocr_adapter: OCRAdapter | None = None,
) -> ScanImportResult:
    """Propose explicitly labeled invoice fields, requiring review on any uncertainty."""

    try:
        parsed_request = request if isinstance(request, ScanImportRequest) else ScanImportRequest.model_validate(request)
    except ValidationError as exc:
        return _failure_result(
            "unknown",
            "invalid_request",
            "request",
            f"scan request failed schema validation ({len(exc.errors())} issue(s))",
        )

    try:
        loaded = _load_source(parsed_request, input_root)
    except ScanInputError as exc:
        return _failure_result(parsed_request.document_id, exc.code, exc.path, str(exc))
    source_path = loaded.path
    extracted_pages = loaded.pages
    over_limit_pages = loaded.over_limit_pages
    is_image = loaded.kind in IMAGE_KINDS

    page_evidence: list[ScanPageEvidence] = []
    page_fields: list[tuple[dict[str, object], dict[str, list[ScanFieldEvidence]]]] = []
    issues: list[ScanImportIssue] = []
    ocr_pages_used = 0
    for page_number, embedded_text in enumerate(extracted_pages, start=1):
        issue_code: str | None = None
        if page_number in over_limit_pages:
            issue_code = "page_text_limit_exceeded"
            issues.append(
                ScanImportIssue(
                    code=issue_code,
                    path=f"pages[{page_number}]",
                    message="page text exceeded the 25,000 character limit",
                )
            )
            page_evidence.append(
                ScanPageEvidence(page_number=page_number, method="none", character_count=0, issue_code=issue_code)
            )
            continue

        text = embedded_text
        method: Literal["embedded_text", "ocr", "none"] = "embedded_text" if embedded_text.strip() else "none"
        needs_ocr = is_image or (loaded.kind == "pdf" and len(embedded_text.strip()) < MIN_EMBEDDED_TEXT_CHARS)
        if needs_ocr:
            if ocr_adapter is None:
                issue_code = "ocr_unavailable"
                issues.append(
                    ScanImportIssue(
                        code=issue_code,
                        path=f"pages[{page_number}]",
                        message=(
                            "this image needs local OCR; install Tesseract (tesseract-ocr and tesseract-ocr-tha)"
                            if is_image
                            else "this page needs local OCR; install Poppler and Tesseract or provide a text PDF"
                        ),
                    )
                )
            elif ocr_pages_used >= MAX_OCR_PAGES_PER_DOCUMENT:
                issue_code = "ocr_page_limit_exceeded"
                issues.append(
                    ScanImportIssue(
                        code=issue_code,
                        path=f"pages[{page_number}]",
                        message="the local OCR limit is 10 pages per document",
                    )
                )
            else:
                try:
                    if is_image:
                        text = ocr_adapter.extract_image(source_path, locale=parsed_request.locale)
                    else:
                        text = ocr_adapter.extract_page(source_path, page_number, locale=parsed_request.locale)
                    if len(text) > MAX_PAGE_TEXT_CHARS:
                        raise OCRFailure("recognized page exceeded the text limit")
                    method = "ocr"
                    ocr_pages_used += 1
                except Exception:  # noqa: BLE001 - an adapter failure must become an abstention, without leaking details.
                    issue_code = "ocr_failed"
                    issues.append(
                        ScanImportIssue(
                            code=issue_code,
                            path=f"pages[{page_number}]",
                            message="local OCR failed or its output exceeded the page limit",
                        )
                    )
                    text = embedded_text

        if len(text) > MAX_PAGE_TEXT_CHARS:
            issue_code = "page_text_limit_exceeded"
            issues.append(
                ScanImportIssue(
                    code=issue_code,
                    path=f"pages[{page_number}]",
                    message="page text exceeded the 25,000 character limit",
                )
            )
            page_evidence.append(
                ScanPageEvidence(page_number=page_number, method="none", character_count=0, issue_code=issue_code)
            )
            continue

        page_evidence.append(
            ScanPageEvidence(
                page_number=page_number,
                method=method,
                character_count=len(text),
                issue_code=issue_code,
            )
        )
        if method == "none" or not text.strip():
            continue
        extraction = RuleBasedExtractor().extract(text, locale=parsed_request.locale)
        excerpts = _labeled_excerpts(text)
        field_evidence = {
            name: _proposal_evidence(name, extraction, excerpts, page_number=page_number, method=method)
            for name in extraction.fields
        }
        page_fields.append((extraction.fields, field_evidence))
        for issue in extraction.issues:
            issues.append(
                ScanImportIssue(
                    code=issue["code"],
                    path=f"pages[{page_number}].{issue['path']}",
                    message=issue["message"],
                )
            )

    fields, ambiguous_fields = _merge_field_values(page_fields, issues)
    missing_required = [
        name for name in REQUIRED_FIELDS if name not in fields or fields[name].status != "proposed"
    ]
    status: Literal["complete", "needs_input"] = (
        "needs_input" if missing_required or ambiguous_fields or issues else "complete"
    )
    provenance_complete = all(bool(proposal.evidence) for proposal in fields.values())
    result = ScanImportResult(
        document_id=parsed_request.document_id,
        status=status,
        fields=fields,
        missing_required=missing_required,
        ambiguous_fields=ambiguous_fields,
        issues=issues,
        page_evidence=page_evidence,
        manifest=ScanImportManifest(
            source_sha256=hashlib.sha256(loaded.contents).hexdigest(),
            source_byte_size=len(loaded.contents),
            page_count=len(extracted_pages),
            ocr_pages_used=ocr_pages_used,
        ),
        model_calls=0,
        qa=ScanImportQA(
            field_provenance_complete=provenance_complete,
            required_fields_complete=not missing_required,
            no_ambiguous_fields=not ambiguous_fields,
            accepted=status == "complete" and provenance_complete,
        ),
    )
    return result
