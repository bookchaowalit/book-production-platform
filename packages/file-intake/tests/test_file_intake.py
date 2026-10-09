from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from file_intake import TableReadError, detect, read_table, routes_for
from file_intake.__main__ import main

_CT = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
_WB = (
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'
)
_RELS = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'
_SHEET = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>'
_STYLES = (
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
    '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs></styleSheet>'
)


def xlsx(path: Path, sheets: dict[str, str], shared: list[str] | None = None, *, styles: bool = True,
         extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CT)
        archive.writestr("xl/workbook.xml", _WB.format(sheets="".join(
            f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(sheets))))
        archive.writestr("xl/_rels/workbook.xml.rels", _RELS.format(rels="".join(
            f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))))
        for i, rows in enumerate(sheets.values()):
            archive.writestr(f"xl/worksheets/sheet{i + 1}.xml", _SHEET.format(rows=rows))
        if shared is not None:
            archive.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                             + "".join(f"<si><t>{text}</t></si>" for text in shared) + "</sst>")
        if styles:
            archive.writestr("xl/styles.xml", _STYLES)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


ORDERS = (
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>'
    '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><v>120.5</v></c><c r="C2" s="1"><v>46023</v></c><c r="D2" t="b"><v>1</v></c></row>'
    '<row r="3"><c r="A3" t="inlineStr"><is><t>ภาคใต้</t></is></c><c r="B3"><f>B2*2</f><v>241</v></c><c r="C3" s="2"><v>46024</v></c></row>'
    '<row r="5"><c r="B5"><f>SUM(B2:B3)</f></c></row>'
)


def test_xlsx_reads_shared_inline_numbers_dates_booleans_and_formula_cache(tmp_path: Path) -> None:
    path = xlsx(tmp_path / "orders.xlsx", {"Orders": ORDERS, "Other": ""}, ["region", "amount", "sold_on", "paid", "ภาคเหนือ"])
    table = read_table(path)

    assert table.header == ["region", "amount", "sold_on", "paid"]
    assert table.rows[:2] == [["ภาคเหนือ", "120.5", "2026-01-01", "TRUE"], ["ภาคใต้", "241", "2026-01-02", ""]]
    assert len(table.rows) == 2  # the formula row without a cached value is blank and trimmed
    assert "formula_without_cached_value" in table.notes
    assert (table.sheet, table.sheets) == ("Orders", ["Orders", "Other"])
    assert table.to_csv_text().splitlines()[0] == "region,amount,sold_on,paid"


def test_xlsx_numbers_use_excel_precision(tmp_path: Path) -> None:
    rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>v</t></is></c></row>'
            '<row r="2"><c r="A2"><v>99.98999999999999</v></c></row><row r="3"><c r="A3"><v>0.30000000000000004</v></c></row>'
            '<row r="4"><c r="A4"><v>12345678901234567</v></c></row><row r="5"><c r="A5"><v>1.5E-20</v></c></row>')
    table = read_table(xlsx(tmp_path / "n.xlsx", {"S": rows}))
    assert [row[0] for row in table.rows] == ["99.99", "0.3", "12345678901234567", "1.5E-20"]


def test_xlsx_named_sheet_and_missing_sheet(tmp_path: Path) -> None:
    second = '<row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>x</t></is></c></row>'
    path = xlsx(tmp_path / "book.xlsx", {"First": ORDERS, "Second": second}, ["region", "amount", "sold_on", "paid", "ภาคเหนือ"])
    assert read_table(path, sheet="Second").rows == [["x"]]
    with pytest.raises(TableReadError) as caught:
        read_table(path, sheet="Nope")
    assert caught.value.code == "sheet_not_found"


def test_xlsx_with_doctype_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evil.xlsx"
    xlsx(path, {"S": ORDERS}, ["a", "b", "c", "d", "e"])
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>&lol;</t></si></sst>'
    rebuilt = tmp_path / "evil2.xlsx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for info in source.infolist():
            target.writestr(info, bomb if info.filename == "xl/sharedStrings.xml" else source.read(info))
    with pytest.raises(TableReadError) as caught:
        read_table(rebuilt)
    assert caught.value.code == "xlsx_unsafe_xml"


def test_xlsx_zip_bomb_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from file_intake import tables

    path = xlsx(tmp_path / "big.xlsx", {"S": ORDERS}, ["a", "b", "c", "d", "e"], extra={"xl/padding.bin": b"0" * 50_000})
    monkeypatch.setattr(tables, "MAX_UNCOMPRESSED_BYTES", 10_000)
    with pytest.raises(TableReadError) as caught:
        read_table(path)
    assert caught.value.code == "xlsx_too_large"


@pytest.mark.parametrize(
    ("content", "encoding", "delimiter"),
    [
        ("ภูมิภาค,ยอดขาย\nเหนือ,10\n", "utf-8", ","),
        ("﻿region;total\nA;10\n", "utf-8", ";"),
        ("ภูมิภาค\tยอดขาย\nเหนือ\t10\n", "cp874", "\t"),
    ],
)
def test_csv_encodings_and_delimiters(tmp_path: Path, content: str, encoding: str, delimiter: str) -> None:
    path = tmp_path / "data.csv"
    path.write_bytes(content.encode(encoding))
    table = read_table(path)
    assert table.delimiter == delimiter
    assert len(table.header) == 2 and table.rows[0][1] == "10"
    assert table.encoding == ("cp874" if encoding == "cp874" else "utf-8-sig")


def test_csv_trims_blank_edges_and_flags_header_problems(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("\n\na,a,\n1,2,\n,,\n", encoding="utf-8")
    table = read_table(path)
    assert table.header == ["a", "a"] and table.rows == [["1", "2"]]
    assert "duplicate_header" in table.notes


@pytest.mark.parametrize(
    ("name", "data", "kind", "issue"),
    [
        ("a.pdf", b"%PDF-1.7\n", "pdf", None),
        ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "png", None),
        ("a.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20, "jpeg", None),
        ("a.glb", b"glTF\x02\x00\x00\x00", "glb", None),
        ("a.mp4", b"\x00\x00\x00\x18ftypisom", "video", None),
        ("a.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio", None),
        ("a.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "png", "extension_mismatch"),
        ("a.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 20, "legacy_office", "legacy_office_unsupported"),
        ("a.xlsx", b"\xd0\xcf\x11\xe0" + b"\x00" * 20, "legacy_office", "encrypted_or_legacy_office"),
        ("a.exe", b"MZ\x90\x00\x03", "unknown", "unsupported_type"),
        ("notes.txt", "บันทึก".encode(), "unknown", "unsupported_type"),
    ],
)
def test_detect_by_content(tmp_path: Path, name: str, data: bytes, kind: str, issue: str | None) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    result = detect(path)
    assert (result.kind, result.issue) == (kind, issue)


def test_detect_office_zips(tmp_path: Path) -> None:
    book = xlsx(tmp_path / "b.xlsx", {"S": ORDERS}, ["a", "b", "c", "d", "e"])
    assert detect(book).kind == "xlsx"
    document = tmp_path / "d.docx"
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    assert detect(document).kind == "docx"
    renamed = tmp_path / "d.xlsx"
    renamed.write_bytes(document.read_bytes())
    assert detect(renamed).issue == "extension_mismatch"


def test_non_tables_are_refused_and_routes_cover_tools(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(TableReadError) as caught:
        read_table(pdf)
    assert caught.value.code == "not_a_table"
    assert {route.tool_id for route in routes_for("xlsx")} == {"data-viz-v1a", "spreadsheet-v1a"}
    assert {route.tool_id for route in routes_for("docx")} == {"documents-v1a"}
    assert routes_for("unknown") == ()


def test_cli_inspect_reports_table_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = xlsx(tmp_path / "orders.xlsx", {"Orders": ORDERS}, ["region", "amount", "sold_on", "paid", "ภาคเหนือ"])
    assert main(["inspect", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["file"]["kind"] == "xlsx" and report["table"]["columns"] == 4
    assert [route["tool_id"] for route in report["routes"]] == ["data-viz-v1a", "spreadsheet-v1a"]
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ\x90\x00")
    assert main(["inspect", str(bad)]) == 2


def test_sparse_xlsx_is_refused_before_building_a_grid(tmp_path: Path) -> None:
    rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
            '<row r="100000"><c r="ADZ100000" t="inlineStr"><is><t>far</t></is></c></row>')
    with pytest.raises(TableReadError) as caught:
        read_table(xlsx(tmp_path / "sparse.xlsx", {"S": rows}))
    assert caught.value.code == "table_too_large"
    wide = '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c><c r="GZ1" t="inlineStr"><is><t>b</t></is></c></row>'
    with pytest.raises(TableReadError) as caught:
        read_table(xlsx(tmp_path / "wide.xlsx", {"S": wide}))
    assert caught.value.code == "table_too_large"
