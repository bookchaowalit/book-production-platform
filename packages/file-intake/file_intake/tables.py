"""Read a CSV or XLSX file into a plain header + rows table of strings.

Standard library only. Untrusted input is bounded: file size, uncompressed
ZIP size, rows, columns, and cell length are capped, XML with a DOCTYPE is
refused (no entity expansion), and formulas are never evaluated: a formula
cell yields its cached value, or an empty cell plus a note when there is none.
Numbers are shown the way Excel displays them (15 significant digits); date-formatted numbers become ISO
dates. Types are decided by each tool's own validator, not here.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from .detect import detect

MAX_TABLE_FILE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 32_767
MAX_CELLS = 2_000_000  # rows x columns of the materialized sheet
CSV_ENCODINGS = ("utf-8-sig", "cp874")

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")
# Built-in Excel number formats that display a date or time.
_DATE_FORMAT_IDS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})
_DATE_TOKENS = re.compile(r"(?<!\\)[dmyhs]", re.IGNORECASE)
_EXCEL_EPOCH = dt.datetime(1899, 12, 30, tzinfo=dt.UTC)


class TableReadError(ValueError):
    """The file cannot be read as a table; ``code`` is stable and safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    source_kind: str
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    encoding: str | None = None
    delimiter: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_csv_text(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.header)
        writer.writerows(self.rows)
        return buffer.getvalue()

    def to_dict(self) -> dict[str, object]:
        return {
            "header": list(self.header),
            "rows": [list(row) for row in self.rows],
            "source_kind": self.source_kind,
            "sheet": self.sheet,
            "sheets": list(self.sheets),
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "notes": list(self.notes),
        }


def read_table(path: str | Path, *, sheet: str | None = None) -> Table:
    """Read the first non-empty row as the header and the rest as rows."""

    file = Path(path)
    detection = detect(file)
    if detection.issue is not None:
        raise TableReadError(detection.issue, f"file cannot be used: {detection.issue}")
    if detection.size > MAX_TABLE_FILE_BYTES:
        raise TableReadError("file_too_large", "table files may be at most 20 MiB")
    if detection.kind == "csv":
        if sheet is not None:
            raise TableReadError("sheet_not_supported", "a CSV file has no sheets")
        return _read_csv(file)
    if detection.kind == "xlsx":
        return _read_xlsx(file, sheet)
    raise TableReadError("not_a_table", f"{detection.kind} files are not tables; use CSV or XLSX")


def _shape(raw_rows: list[list[str]], table: Table) -> Table:
    rows = [[cell.strip() if isinstance(cell, str) else cell for cell in row] for row in raw_rows]
    while rows and not any(rows[0]):
        rows.pop(0)
    if not rows:
        raise TableReadError("table_empty", "the table has no header row")
    width = max(len(row) for row in rows)
    header = rows[0] + [""] * (width - len(rows[0]))
    while width and not header[width - 1] and not any(len(row) >= width and row[width - 1] for row in rows[1:]):
        width -= 1
        header = header[:width]
    if width == 0:
        raise TableReadError("table_empty", "the table has no header row")
    if width > MAX_COLUMNS:
        raise TableReadError("too_many_columns", f"a table may have at most {MAX_COLUMNS} columns")
    body = [(row + [""] * (width - len(row)))[:width] for row in rows[1:]]
    while body and not any(body[-1]):
        body.pop()
    if len(body) > MAX_ROWS:
        raise TableReadError("too_many_rows", f"a table may have at most {MAX_ROWS} rows")
    if any(not name for name in header):
        table.notes.append("blank_header_cell")
    if len(set(header)) != len(header):
        table.notes.append("duplicate_header")
    table.header, table.rows = header, body
    return table


def _read_csv(file: Path) -> Table:
    data = file.read_bytes()
    for encoding in CSV_ENCODINGS:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp874 decodes nearly everything; kept for safety
        raise TableReadError("csv_encoding_unknown", "CSV must be UTF-8 or Thai Windows (cp874)")
    if "\x00" in text:
        raise TableReadError("csv_binary", "the file is not text")
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = "\t" if file.suffix.lower() == ".tsv" else ","
    csv.field_size_limit(MAX_CELL_CHARS)
    try:
        raw_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise TableReadError("malformed_csv", "the CSV could not be parsed") from exc
    table = Table(header=[], rows=[], source_kind="csv", encoding=encoding, delimiter=delimiter)
    if encoding == "cp874":
        table.notes.append("decoded_as_cp874")
    return _shape(raw_rows, table)


def _xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        data = archive.read(name)
    except KeyError as exc:
        raise TableReadError("xlsx_invalid", f"workbook part is missing: {name}") from exc
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data.upper():
        raise TableReadError("xlsx_unsafe_xml", "workbook XML declares a DOCTYPE or entity")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise TableReadError("xlsx_invalid", "workbook XML is malformed") from exc


def _column_index(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - 64)
    return value - 1


def _date_styles(archive: zipfile.ZipFile) -> set[int]:
    if "xl/styles.xml" not in archive.namelist():
        return set()
    root = _xml(archive, "xl/styles.xml")
    custom: dict[int, str] = {}
    formats = root.find(f"{_MAIN}numFmts")
    if formats is not None:
        for item in formats.findall(f"{_MAIN}numFmt"):
            try:
                custom[int(item.get("numFmtId", "-1"))] = item.get("formatCode", "")
            except ValueError:
                continue
    dated: set[int] = set()
    xfs = root.find(f"{_MAIN}cellXfs")
    if xfs is None:
        return dated
    for index, xf in enumerate(xfs.findall(f"{_MAIN}xf")):
        try:
            format_id = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(format_id, ""))
        if format_id in _DATE_FORMAT_IDS or (format_id in custom and _DATE_TOKENS.search(code)):
            dated.add(index)
    return dated


def _excel_number(raw: str) -> str:
    """The number as Excel shows it: at most 15 significant digits.

    Files store binary floats (``99.99`` is written as ``99.98999999999999``);
    Excel displays and compares them at 15 significant digits, so do the same.
    """

    if re.fullmatch(r"-?\d{1,15}", raw):
        return raw
    try:
        number = float(raw)
    except ValueError:
        return raw
    text = format(number, ".15g")
    return raw if "e" in text or "inf" in text or "nan" in text else text


def _style_index(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _excel_date(serial: str) -> str | None:
    try:
        number = float(serial)
    except ValueError:
        return None
    if not 0 <= number < 2_958_466:
        return None
    moment = _EXCEL_EPOCH + dt.timedelta(days=number)
    if moment.hour == moment.minute == moment.second == 0 and number == int(number):
        return moment.date().isoformat()
    return moment.replace(microsecond=0, tzinfo=None).isoformat(sep=" ")


def _read_xlsx(file: Path, sheet: str | None) -> Table:
    try:
        archive = zipfile.ZipFile(file)
    except (zipfile.BadZipFile, OSError) as exc:
        raise TableReadError("xlsx_invalid", "the workbook is not a valid XLSX file") from exc
    with archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise TableReadError("xlsx_too_large", "the workbook expands beyond 100 MiB")
        if any(info.flag_bits & 0x1 for info in infos):
            raise TableReadError("xlsx_encrypted", "encrypted workbooks are not supported")

        workbook = _xml(archive, "xl/workbook.xml")
        rels = _xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {rel.get("Id"): rel.get("Target", "") for rel in rels.findall(f"{_PKG_REL}Relationship")}
        sheets: list[tuple[str, str]] = []
        sheets_node = workbook.find(f"{_MAIN}sheets")
        for node in sheets_node.findall(f"{_MAIN}sheet") if sheets_node is not None else []:
            target = targets.get(node.get(f"{_REL}id"), "")
            target = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            sheets.append((node.get("name", ""), target))
        if not sheets:
            raise TableReadError("xlsx_no_sheets", "the workbook has no sheets")
        names = [name for name, _ in sheets]
        if sheet is None:
            chosen = sheets[0]
        else:
            matches = [item for item in sheets if item[0] == sheet]
            if not matches:
                raise TableReadError("sheet_not_found", f"sheet not found; available: {', '.join(names)}")
            chosen = matches[0]
        if ".." in PurePosixPath(chosen[1]).parts:
            raise TableReadError("xlsx_invalid", "sheet path is not inside the workbook")

        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in _xml(archive, "xl/sharedStrings.xml").findall(f"{_MAIN}si"):
                shared.append("".join(text.text or "" for text in item.iter(f"{_MAIN}t")))
        dated = _date_styles(archive)
        root = _xml(archive, chosen[1])

    table = Table(header=[], rows=[], source_kind="xlsx", sheet=chosen[0], sheets=names)
    data = root.find(f"{_MAIN}sheetData")
    grid: dict[int, dict[int, str]] = {}
    formula_without_value = False
    for row in data.findall(f"{_MAIN}row") if data is not None else []:
        for cell in row.findall(f"{_MAIN}c"):
            match = _CELL_REF.match(cell.get("r", ""))
            if match is None:
                continue
            column, row_index = _column_index(match.group(1)), int(match.group(2)) - 1
            if column >= MAX_COLUMNS * 4 or row_index > MAX_ROWS + 1:
                raise TableReadError("table_too_large", "the sheet is larger than the table limits")
            kind = cell.get("t", "n")
            value_node = cell.find(f"{_MAIN}v")
            raw = value_node.text if value_node is not None and value_node.text is not None else None
            if kind == "inlineStr":
                inline = cell.find(f"{_MAIN}is")
                value = "".join(text.text or "" for text in inline.iter(f"{_MAIN}t")) if inline is not None else ""
            elif raw is None:
                if cell.find(f"{_MAIN}f") is not None:
                    formula_without_value = True
                value = ""
            elif kind == "s":
                try:
                    value = shared[int(raw)]
                except (ValueError, IndexError) as exc:
                    raise TableReadError("xlsx_invalid", "a cell points at a missing shared string") from exc
            elif kind == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            elif kind == "e":
                value = ""
                table.notes.append("error_cell")
            elif kind == "n" and _style_index(cell.get("s")) in dated:
                value = _excel_date(raw) or raw
            elif kind == "n":
                value = _excel_number(raw)
            else:
                value = raw
            if len(value) > MAX_CELL_CHARS:
                raise TableReadError("cell_too_long", "a cell is longer than 32,767 characters")
            grid.setdefault(row_index, {})[column] = value
    if formula_without_value:
        table.notes.append("formula_without_cached_value")
    if not grid:
        raise TableReadError("table_empty", "the sheet is empty")
    last_row = max(grid)
    last_column = max(column for cells in grid.values() for column in cells)
    # Check the extent before building a dense grid: a sparse sheet (A1 plus one far cell)
    # must be refused, not expanded into millions of empty cells.
    if last_column + 1 > MAX_COLUMNS or last_row > MAX_ROWS or (last_row + 1) * (last_column + 1) > MAX_CELLS:
        raise TableReadError("table_too_large", "the sheet is larger than the table limits")
    raw_rows = [[grid.get(r, {}).get(c, "") for c in range(last_column + 1)] for r in range(last_row + 1)]
    table.notes = sorted(set(table.notes))
    return _shape(raw_rows, table)


__all__ = [
    "MAX_CELLS",
    "MAX_COLUMNS",
    "MAX_ROWS",
    "MAX_TABLE_FILE_BYTES",
    "MAX_UNCOMPRESSED_BYTES",
    "Table",
    "TableReadError",
    "read_table",
]
