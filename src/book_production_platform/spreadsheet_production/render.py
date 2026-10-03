"""XLSX renderer for validated WorkbookIR.

The layout constants below (styles, number formats, widths, freeze panes) are
the single source of truth for both the renderer and the manual guide
(:mod:`.manual`), so the click-by-click Excel steps describe exactly what this
module writes.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter  # type: ignore[import-untyped]

from book_production_platform.spreadsheet_ir import (
    DEFAULT_NUMBER_FORMATS,
    ArtifactRef,
    CellIR,
    FormulaSpec,
    SheetIR,
    WorkbookIR,
)

from .formulas import EvaluationResult, evaluate_workbook

SUMMARY_SHEET = "Summary"
TITLE_STYLE: dict[str, Any] = {"bold": True, "font_size": 16}
HEADER_STYLE: dict[str, Any] = {"bold": True, "bg_color": "#D9EAF7", "border": 1}
FORMULA_FILL = "#FFF2CC"
FORMULA_NUMBER_FORMATS: dict[str, str] = {"decimal": "0.00", "integer": "0"}
DATA_COLUMN_WIDTH = 18
SUMMARY_COLUMN_WIDTHS: tuple[int, int] = (24, 18)
FREEZE_ROWS = 1
CONDITIONAL_CRITERIA: dict[str, str] = {
    "greater_than": ">",
    "greater_than_or_equal": ">=",
    "less_than": "<",
    "less_than_or_equal": "<=",
    "equal_to": "==",
    "not_equal_to": "!=",
    "between": "between",
}
_CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")


class RenderError(RuntimeError):
    """Raised when an XLSX artifact cannot be written."""


def column_number(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def split_cell(cell: str) -> tuple[str, int]:
    match = _CELL_RE.fullmatch(cell)
    if match is None:
        raise RenderError("cell_reference_invalid")
    return match.group(1), int(match.group(2))


def _coordinates(address: str) -> tuple[str, int, int]:
    sheet, cell = address.split("!", 1)
    letters, row = split_cell(cell)
    return sheet, row - 1, column_number(letters) - 1


def header_row(sheet_name: str) -> int:
    """1-based row that holds the column headers (Summary keeps its title above)."""

    return 3 if sheet_name == SUMMARY_SHEET else 1


def absolute_reference(reference: str) -> str:
    """``Detail!B2:B3`` -> ``='Detail'!$B$2:$B$3`` (multi-letter columns included)."""

    sheet_name, cells = reference.split("!", 1)
    start, end = cells.split(":")
    (start_col, start_row), (end_col, end_row) = split_cell(start), split_cell(end)
    return f"='{sheet_name}'!${start_col}${start_row}:${end_col}${end_row}"


def number_text(value: Decimal) -> str:
    """Plain decimal text without exponent, as typed into an Excel dialog."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def formula_number_format(formula: FormulaSpec) -> str | None:
    return FORMULA_NUMBER_FORMATS.get(formula.result_type)


def _artifact(path: Path) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
    )


def _cached_value(value: object) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


class _Formats:
    def __init__(self, workbook: Any) -> None:
        self.workbook = workbook
        self.title = workbook.add_format(TITLE_STYLE)
        self.header = workbook.add_format(HEADER_STYLE)
        self.text = workbook.add_format({"text_wrap": True})
        self.boolean = workbook.add_format({"text_wrap": True})
        self.blank = workbook.add_format({})
        self._numbers: dict[str, Any] = {}
        self._formulas: dict[str | None, Any] = {}
        self._fills: dict[str, Any] = {}

    def number(self, number_format: str) -> Any:
        if number_format not in self._numbers:
            self._numbers[number_format] = self.workbook.add_format({"num_format": number_format})
        return self._numbers[number_format]

    def formula(self, number_format: str | None) -> Any:
        if number_format not in self._formulas:
            style: dict[str, Any] = {"bold": True, "bg_color": FORMULA_FILL}
            if number_format is not None:
                style["num_format"] = number_format
            self._formulas[number_format] = self.workbook.add_format(style)
        return self._formulas[number_format]

    def fill(self, color: str) -> Any:
        if color not in self._fills:
            self._fills[color] = self.workbook.add_format({"bg_color": color})
        return self._fills[color]


def _column_format_map(sheet: SheetIR) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in sheet.column_formats:
        start, _ = item.range.split(":")
        mapping[split_cell(start)[0]] = item.number_format
    return mapping


def _write_value(worksheet: Any, row: int, column: int, cell: CellIR, formats: _Formats, number_format: str | None) -> None:
    value = cell.value
    if cell.data_type == "null" or value is None:
        worksheet.write_blank(row, column, None, formats.blank)
    elif cell.data_type == "text":
        worksheet.write_string(row, column, str(value), formats.text)
    elif cell.data_type == "integer":
        if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
            raise RenderError("integer_value_invalid")
        worksheet.write_number(row, column, int(value), formats.number(number_format or DEFAULT_NUMBER_FORMATS["integer"]))
    elif cell.data_type == "decimal":
        if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
            raise RenderError("decimal_value_invalid")
        worksheet.write_number(row, column, float(value), formats.number(number_format or DEFAULT_NUMBER_FORMATS["decimal"]))
    elif cell.data_type == "date":
        if not isinstance(value, date):
            raise RenderError("date_value_invalid")
        worksheet.write_datetime(row, column, datetime.combine(value, time()), formats.number(number_format or DEFAULT_NUMBER_FORMATS["date"]))
    elif cell.data_type == "boolean":
        worksheet.write_boolean(row, column, bool(value), formats.boolean)
    else:
        raise RenderError(f"unsupported_cell_type:{cell.data_type}")


def _range_bounds(cell_range: str) -> tuple[int, int, int, int]:
    start, end = cell_range.split(":")
    (start_col, start_row), (end_col, end_row) = split_cell(start), split_cell(end)
    return start_row - 1, column_number(start_col) - 1, end_row - 1, column_number(end_col) - 1


def _write_sheet_features(worksheet: Any, sheet: SheetIR, formats: _Formats) -> None:
    for validation in sheet.data_validations:
        worksheet.data_validation(*_range_bounds(validation.range), {
            "validate": "list", "source": list(validation.allowed_values), "ignore_blank": True,
        })
    for rule in sheet.conditional_formats:
        options: dict[str, Any] = {"type": "cell", "criteria": CONDITIONAL_CRITERIA[rule.operator], "format": formats.fill(rule.fill_color)}
        if rule.operator == "between":
            options["minimum"] = number_text(rule.value)
            options["maximum"] = number_text(rule.value2 if rule.value2 is not None else rule.value)
        else:
            options["value"] = number_text(rule.value)
        worksheet.conditional_format(*_range_bounds(rule.range), options)


def render_workbook(ir: WorkbookIR, output_path: str | Path) -> list[ArtifactRef]:
    """Write a reopenable XLSX with formula cached values from the evaluator."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    evaluation: EvaluationResult = evaluate_workbook(ir)
    workbook = xlsxwriter.Workbook(str(path))
    workbook.set_properties({"title": ir.title, "comments": "Generated by Spreadsheet V1A deterministic recipe"})
    workbook.set_calc_mode("auto")
    formats = _Formats(workbook)
    formula_map = {formula.target: formula for formula in ir.formulas}
    written: set[str] = set()
    try:
        for sheet in ir.sheets:
            worksheet = workbook.add_worksheet(sheet.name)
            worksheet.freeze_panes(FREEZE_ROWS, 0)
            headers_at = header_row(sheet.name) - 1
            for column_index, label in enumerate(sheet.column_labels):
                worksheet.write(headers_at, column_index, label, formats.header)
            column_formats = _column_format_map(sheet)
            for cell in sheet.cells:
                sheet_name, row, column = _coordinates(cell.address)
                if sheet_name != sheet.name:
                    raise RenderError("cell_sheet_mismatch")
                formula = formula_map.get(cell.address)
                if formula is not None:
                    cached = evaluation.values.get(cell.address)
                    worksheet.write_formula(row, column, formula.expression, formats.formula(formula_number_format(formula)), _cached_value(cached))
                elif row == headers_at and column < len(sheet.column_labels) and cell.data_type == "text":
                    worksheet.write_string(row, column, str(cell.value), formats.header)
                elif sheet.name == SUMMARY_SHEET and (row, column) == (0, 0) and cell.data_type == "text":
                    worksheet.write_string(row, column, str(cell.value), formats.title)
                else:
                    letters = cell.address.split("!", 1)[1].rstrip("0123456789")
                    _write_value(worksheet, row, column, cell, formats, column_formats.get(letters))
                written.add(cell.address)
            for formula in ir.formulas:
                if formula.target in written or formula.target.split("!", 1)[0] != sheet.name:
                    continue
                _, row, column = _coordinates(formula.target)
                worksheet.write_formula(
                    row, column, formula.expression, formats.formula(formula_number_format(formula)), _cached_value(evaluation.values[formula.target])
                )
                written.add(formula.target)
            if sheet.name != SUMMARY_SHEET:
                worksheet.set_column(0, max(0, len(sheet.column_labels) - 1), DATA_COLUMN_WIDTH)
            else:
                worksheet.set_column(0, 0, SUMMARY_COLUMN_WIDTHS[0])
                worksheet.set_column(1, 1, SUMMARY_COLUMN_WIDTHS[1])
            _write_sheet_features(worksheet, sheet, formats)
        for name, reference in ir.named_ranges.items():
            workbook.define_name(name, absolute_reference(reference))
        workbook.close()
    except Exception as exc:
        with contextlib.suppress(Exception):  # the original render failure is what matters
            workbook.close()
        path.unlink(missing_ok=True)
        if isinstance(exc, RenderError):
            raise
        raise RenderError(f"xlsx_render_failed:{exc}") from exc
    return [_artifact(path)]


__all__ = [
    "CONDITIONAL_CRITERIA",
    "DATA_COLUMN_WIDTH",
    "FORMULA_FILL",
    "FORMULA_NUMBER_FORMATS",
    "FREEZE_ROWS",
    "HEADER_STYLE",
    "SUMMARY_COLUMN_WIDTHS",
    "SUMMARY_SHEET",
    "TITLE_STYLE",
    "RenderError",
    "absolute_reference",
    "column_number",
    "formula_number_format",
    "header_row",
    "number_text",
    "render_workbook",
    "split_cell",
]
