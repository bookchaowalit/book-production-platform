"""DOCX and image sources for the deterministic scan import (no AI, model_calls=0)."""

import json
import struct
import zipfile
import zlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from document_production.cli import main
from document_production.scan_import import TesseractOCRAdapter, run_scan_import
from docx import Document

LABELED_LINES = [
    "title: Synthetic invoice",
    "customer: Example customer",
    "date: 2026-09-22",
    "currency: USD",
    "item: Design service | 2 | 10.00 | 0",
]


def create_docx(path: Path, paragraphs: list[str], table_rows: list[list[str]] | None = None) -> None:
    document = Document()
    for line in paragraphs:
        document.add_paragraph(line)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=max(len(row) for row in table_rows))
        for row_index, row in enumerate(table_rows):
            for column_index, value in enumerate(row):
                table.cell(row_index, column_index).text = value
    document.save(str(path))


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b"".join(b"\x00" + b"\xff" * width for _ in range(min(height, 4))))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def jpeg_bytes(width: int = 16, height: int = 8) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def tiff_bytes(width: int, height: int) -> bytes:
    entries = [(256, 4, 1, width), (257, 4, 1, height)]
    body = struct.pack("<H", len(entries)) + b"".join(struct.pack("<HHII", *entry) for entry in entries)
    return b"II*\x00" + struct.pack("<I", 8) + body + struct.pack("<I", 0)


def webp_vp8x_bytes(width: int, height: int) -> bytes:
    payload = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


class FakeImageOCR:
    def __init__(self, text: str = "\n".join(LABELED_LINES)):
        self.text = text
        self.calls: list[tuple[Path, str]] = []

    def extract_page(self, pdf_path: Path, page_number: int, *, locale: str) -> str:
        raise AssertionError("image sources must not use PDF page OCR")

    def extract_image(self, image_path: Path, *, locale: str) -> str:
        self.calls.append((image_path, locale))
        return self.text


def scan(tmp_path: Path, name: str, **kwargs):
    return run_scan_import(
        {"document_id": f"scan-{name}", "source_file": name, "locale": "en-US"},
        input_root=tmp_path,
        **kwargs,
    )


def test_docx_with_labeled_paragraphs_completes_with_embedded_text_evidence(tmp_path):
    create_docx(tmp_path / "invoice.docx", ["Invoice", *LABELED_LINES])

    class NoOCR:
        def extract_page(self, *args, **kwargs):
            raise AssertionError("DOCX must not use OCR")

        def extract_image(self, *args, **kwargs):
            raise AssertionError("DOCX must not use OCR")

    result = scan(tmp_path, "invoice.docx", ocr_adapter=NoOCR())

    assert result.status == "complete", result.issues
    assert result.model_calls == 0
    assert result.qa.accepted is True
    assert result.manifest.page_count == 1
    assert result.manifest.ocr_pages_used == 0
    assert result.manifest.source_byte_size == (tmp_path / "invoice.docx").stat().st_size
    evidence = result.fields["customer_name"].evidence[0]
    assert (evidence.page_number, evidence.method, evidence.source_excerpt) == (
        1,
        "embedded_text",
        "customer: Example customer",
    )
    assert result.page_evidence[0].method == "embedded_text"


def test_docx_table_rows_become_label_lines_and_keep_existing_label_cells(tmp_path):
    create_docx(
        tmp_path / "table.docx",
        ["title: Table invoice"],
        table_rows=[
            ["Customer", "Example customer", ""],
            ["Date", "2026-09-22", ""],
            ["currency: USD", "", ""],
            ["Item", "Design service", "2 | 10.00 | 0"],
            ["Description", "Qty", "Price"],
        ],
    )

    result = scan(tmp_path, "table.docx")

    assert result.status == "complete", result.issues
    assert result.fields["customer_name"].value == "Example customer"
    assert result.fields["customer_name"].evidence[0].source_excerpt == "Customer: Example customer"
    assert result.fields["currency"].evidence[0].source_excerpt == "currency: USD"
    assert result.fields["line_items"].evidence[0].source_excerpt == "Item: Design service | 2 | 10.00 | 0"


def test_docx_text_is_split_into_synthetic_pages_on_line_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("document_production.scan_import.MAX_PAGE_TEXT_CHARS", 60)
    create_docx(tmp_path / "long.docx", LABELED_LINES)

    result = scan(tmp_path, "long.docx")

    assert result.manifest.page_count > 1
    assert all(page.character_count <= 60 for page in result.page_evidence)
    assert result.status == "complete", result.issues
    pages = {proposal.evidence[0].page_number for proposal in result.fields.values()}
    assert len(pages) > 1


def test_docx_over_synthetic_page_limit_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("document_production.scan_import.MAX_PAGE_TEXT_CHARS", 30)
    monkeypatch.setattr("document_production.scan_import.MAX_DOCX_PAGES", 2)
    create_docx(tmp_path / "many.docx", LABELED_LINES)

    result = scan(tmp_path, "many.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_page_limit_exceeded"


def test_docx_zip_bomb_guard_uses_declared_uncompressed_size(tmp_path, monkeypatch):
    create_docx(tmp_path / "bomb.docx", LABELED_LINES)
    declared = sum(entry.file_size for entry in zipfile.ZipFile(tmp_path / "bomb.docx").infolist())
    monkeypatch.setattr("document_production.scan_import.MAX_DOCX_UNCOMPRESSED_BYTES", declared - 1)

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("an oversized archive must be rejected before parsing")

    monkeypatch.setattr("document_production.scan_import.load_docx", unexpected_parse)

    result = scan(tmp_path, "bomb.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_uncompressed_limit_exceeded"


def test_docx_with_highly_compressed_oversize_entry_is_rejected(tmp_path):
    with zipfile.ZipFile(tmp_path / "real-bomb.docx", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        with archive.open("word/media/padding.bin", "w", force_zip64=True) as stream:
            block = b"\x00" * (1024 * 1024)
            for _ in range(101):
                stream.write(block)
    assert (tmp_path / "real-bomb.docx").stat().st_size < 1024 * 1024

    result = scan(tmp_path, "real-bomb.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_uncompressed_limit_exceeded"


def test_ole_bytes_renamed_docx_are_rejected_as_encrypted_or_legacy(tmp_path):
    (tmp_path / "legacy.docx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)

    result = scan(tmp_path, "legacy.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_encrypted_or_legacy"
    assert result.issues[0].path == "source_file"


def test_zip_with_encrypted_entries_is_rejected(tmp_path):
    create_docx(tmp_path / "locked.docx", LABELED_LINES)
    data = bytearray((tmp_path / "locked.docx").read_bytes())
    offset = data.find(b"PK\x01\x02")
    while offset != -1:
        data[offset + 8] |= 0x01
        offset = data.find(b"PK\x01\x02", offset + 4)
    (tmp_path / "locked.docx").write_bytes(bytes(data))

    result = scan(tmp_path, "locked.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_encrypted_or_legacy"


def test_zip_without_word_document_is_rejected(tmp_path):
    with zipfile.ZipFile(tmp_path / "not-word.docx", "w") as archive:
        archive.writestr("readme.txt", "customer: Example")

    result = scan(tmp_path, "not-word.docx")

    assert result.status == "rejected"
    assert result.issues[0].code == "docx_invalid"


@pytest.mark.parametrize(
    ("name", "contents"),
    [
        ("image.docx", png_bytes()),
        ("document.png", b"PK\x03\x04" + b"\x00" * 64),
        ("scan.jpg", png_bytes()),
        ("scan.png", b"%PDF-1.7\n"),
        ("scan.webp", b"plain text"),
        ("disguised.pdf", png_bytes()),
    ],
)
def test_extension_and_magic_bytes_must_agree(tmp_path, name, contents):
    (tmp_path / name).write_bytes(contents)

    result = scan(tmp_path, name, ocr_adapter=FakeImageOCR())

    assert result.status == "rejected"
    assert result.issues[0].code == "source_type_mismatch"


@pytest.mark.parametrize("name", ["notes.txt", "legacy.doc", "sheet.xlsx", "archive.zip", "noext"])
def test_unsupported_extensions_are_rejected(tmp_path, name):
    (tmp_path / name).write_bytes(b"customer: Example")

    result = scan(tmp_path, name)

    assert result.status == "rejected"
    assert result.issues[0].code == "source_type_unsupported"


def test_legacy_source_pdf_field_still_requires_a_pdf_path(tmp_path):
    create_docx(tmp_path / "invoice.docx", LABELED_LINES)

    result = run_scan_import(
        {"document_id": "scan-legacy", "source_pdf": "invoice.docx", "locale": "en-US"},
        input_root=tmp_path,
    )

    assert result.status == "rejected"
    assert result.issues[0].code == "source_path_invalid"
    assert result.issues[0].path == "source_pdf"


def test_source_file_accepts_pdf_and_rejects_paths_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(png_bytes())

    result = scan(tmp_path, "../outside.png", ocr_adapter=FakeImageOCR())

    assert result.status == "rejected"
    assert result.issues[0].code == "source_path_invalid"


@pytest.mark.parametrize(("name", "contents"), [("scan.png", png_bytes()), ("photo.JPEG", jpeg_bytes())])
def test_images_are_ocr_proposals_with_one_page(tmp_path, name, contents):
    (tmp_path / name).write_bytes(contents)
    ocr = FakeImageOCR()

    result = scan(tmp_path, name, ocr_adapter=ocr)

    assert result.status == "complete", result.issues
    assert result.model_calls == 0
    assert len(ocr.calls) == 1
    assert ocr.calls[0][0].name == name
    assert ocr.calls[0][1] == "en-US"
    assert result.manifest.page_count == 1
    assert result.manifest.ocr_pages_used == 1
    assert result.page_evidence[0].method == "ocr"
    assert all(proposal.evidence[0].method == "ocr" for proposal in result.fields.values())


@pytest.mark.parametrize(
    ("name", "contents"),
    [("scan.tif", tiff_bytes(100, 80)), ("scan.webp", webp_vp8x_bytes(100, 80))],
)
def test_tiff_and_webp_headers_are_accepted(tmp_path, name, contents):
    (tmp_path / name).write_bytes(contents)

    result = scan(tmp_path, name, ocr_adapter=FakeImageOCR())

    assert result.status == "complete", result.issues


def test_image_without_ocr_adapter_needs_input_and_names_tesseract(tmp_path):
    (tmp_path / "scan.png").write_bytes(png_bytes())

    result = scan(tmp_path, "scan.png")

    assert result.status == "needs_input"
    assert result.model_calls == 0
    assert result.issues[0].code == "ocr_unavailable"
    assert "tesseract-ocr-tha" in result.issues[0].message
    assert result.page_evidence[0].method == "none"
    assert result.qa.accepted is False


def test_image_ocr_failure_becomes_an_abstention(tmp_path):
    (tmp_path / "scan.png").write_bytes(png_bytes())

    class BrokenOCR(FakeImageOCR):
        def extract_image(self, image_path: Path, *, locale: str) -> str:
            raise RuntimeError("secret detail")

    result = scan(tmp_path, "scan.png", ocr_adapter=BrokenOCR())

    assert result.status == "needs_input"
    assert result.issues[0].code == "ocr_failed"
    assert "secret detail" not in result.issues[0].message


@pytest.mark.parametrize(
    ("name", "contents"),
    [
        ("wide.png", png_bytes(width=12_001, height=1)),
        ("big.png", png_bytes(width=10_000, height=5_001)),
        ("tall.jpg", jpeg_bytes(width=10, height=20_000)),
        ("huge.tiff", tiff_bytes(12_001, 10)),
        ("huge.webp", webp_vp8x_bytes(8_000, 8_000)),
    ],
)
def test_oversize_image_headers_are_rejected_before_ocr(tmp_path, name, contents):
    (tmp_path / name).write_bytes(contents)
    ocr = FakeImageOCR()

    result = scan(tmp_path, name, ocr_adapter=ocr)

    assert result.status == "rejected"
    assert result.issues[0].code == "image_too_large"
    assert ocr.calls == []


def test_truncated_image_header_is_rejected(tmp_path):
    (tmp_path / "cut.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00")

    result = scan(tmp_path, "cut.jpg", ocr_adapter=FakeImageOCR())

    assert result.status == "rejected"
    assert result.issues[0].code == "image_invalid"


def test_image_only_adapter_needs_only_tesseract(monkeypatch):
    monkeypatch.setattr(
        "document_production.scan_import.shutil.which",
        lambda name: "/usr/bin/tesseract" if name == "tesseract" else None,
    )

    assert TesseractOCRAdapter.from_system() is None
    adapter = TesseractOCRAdapter.from_system_for_images()
    assert adapter is not None
    assert adapter.pdftoppm is None


def test_image_ocr_runs_tesseract_with_fixed_arguments(tmp_path, monkeypatch):
    image = tmp_path / "scan.png"
    image.write_bytes(png_bytes())
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        kwargs["stdout"].write(b"customer: Example")

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr("document_production.scan_import.subprocess.run", fake_run)

    text = TesseractOCRAdapter(None, "tesseract").extract_image(image, locale="th-TH")

    assert text == "customer: Example"
    assert calls[0]["command"] == ["tesseract", str(image), "stdout", "-l", "tha+eng", "--psm", "6"]
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == 30
    assert set(calls[0]["env"]) <= {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}


def run_cli(*args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        return main(list(args))


def test_cli_scan_accepts_source_file_docx(tmp_path):
    create_docx(tmp_path / "invoice.docx", LABELED_LINES)
    request = tmp_path / "scan.json"
    request.write_text(json.dumps({"document_id": "cli-docx", "source_file": "invoice.docx", "locale": "en-US"}))
    output = tmp_path / "proposal.json"

    code = run_cli("scan", "--input", str(request), "--input-root", str(tmp_path), "--output", str(output))

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "complete"


def test_cli_scan_uses_the_image_ocr_adapter_for_images(tmp_path, monkeypatch):
    (tmp_path / "scan.png").write_bytes(png_bytes())
    monkeypatch.setattr("document_production.cli.TesseractOCRAdapter.from_system", lambda: None)
    monkeypatch.setattr("document_production.cli.TesseractOCRAdapter.from_system_for_images", lambda: FakeImageOCR())
    request = tmp_path / "scan.json"
    request.write_text(json.dumps({"document_id": "cli-image", "source_file": "scan.png", "locale": "en-US"}))
    output = tmp_path / "proposal.json"

    code = run_cli("scan", "--input", str(request), "--input-root", str(tmp_path), "--output", str(output))

    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert proposal["page_evidence"][0]["method"] == "ocr"


def test_cli_scan_rejects_request_with_both_sources(tmp_path):
    request = tmp_path / "scan.json"
    request.write_text(
        json.dumps({"document_id": "cli-both", "source_pdf": "a.pdf", "source_file": "a.png", "locale": "en-US"})
    )

    code = run_cli("scan", "--input", str(request), "--input-root", str(tmp_path), "--output", str(tmp_path / "out.json"))

    assert code == 2
