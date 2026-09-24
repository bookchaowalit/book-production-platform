"""Compile typed tables into the deterministic WorkbookIR recipe."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from book_production_platform.spreadsheet_ir import (
    CellIR,
    FormulaSpec,
    SheetIR,
    ValidationIssue,
    WorkbookIR,
    WorkbookRequest,
    parse_typed_value,
    validate_request,
)

from .formulas import evaluate_workbook


class WorkbookBuildError(ValueError):
    """Raised when an input cannot enter the fixed workbook recipe."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(", ".join(issue.code for issue in issues))


def _column_name(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


def _cell_type(value: str | int | Decimal | date | bool | None) -> Literal["text", "integer", "decimal", "date", "boolean", "null"]:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, date):
        return "date"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, int):
        return "integer"
    return "text"


def _text_cell(address: str, value: str, source_ref: str) -> CellIR:
    return CellIR(address=address, data_type="text", value=value, source_ref=source_ref)


def build_workbook_ir(request: WorkbookRequest | dict[str, Any]) -> WorkbookIR:
    """Build Summary/Detail sheets and validate all formulas before export."""

    parsed = request if isinstance(request, WorkbookRequest) else WorkbookRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise WorkbookBuildError(issues)
    table = parsed.tables[0]
    detail_cells: list[CellIR] = []
    source_lineage: dict[str, str] = {}
    for column_index, column in enumerate(table.columns, start=1):
        address = f"Detail!{_column_name(column_index)}1"
        detail_cells.append(_text_cell(address, column.label, f"{table.source_ref}:column:{column.key}"))
        source_lineage[address] = f"{table.source_ref}:column:{column.key}"

    data_start = 2
    for row_index, row in enumerate(table.rows, start=data_start):
        for column_index, column in enumerate(table.columns, start=1):
            address = f"Detail!{_column_name(column_index)}{row_index}"
            value = parse_typed_value(row.get(column.key), column.data_type, path=address)
            detail_cells.append(CellIR(address=address, data_type=_cell_type(value), value=value, source_ref=table.source_ref))
            source_lineage[address] = table.source_ref

    summary_cells: list[CellIR] = [
        _text_cell("Summary!A1", parsed.title, "recipe:title"),
        _text_cell("Summary!A3", "Metric", "recipe:header"),
        _text_cell("Summary!B3", "Value", "recipe:header"),
    ]
    formulas: list[FormulaSpec] = []
    named_ranges: dict[str, str] = {}
    next_summary_row = 4
    if table.rows:
        data_end = data_start + len(table.rows) - 1
        for column_index, column in enumerate(table.columns, start=1):
            if column.data_type not in {"integer", "decimal"}:
                continue
            column_letter = _column_name(column_index)
            reference = f"Detail!{column_letter}{data_start}:{column_letter}{data_end}"
            named_ranges[f"Detail_{column.key}"] = reference
            sum_target = f"Summary!B{next_summary_row}"
            result_type: Literal["integer", "decimal"] = "integer" if column.data_type == "integer" else "decimal"
            formulas.append(FormulaSpec(target=sum_target, expression=f"=SUM({reference})", result_type=result_type))
            summary_cells.extend([
                _text_cell(f"Summary!A{next_summary_row}", column.label, "recipe:summary"),
                CellIR(address=sum_target, data_type="null", value=None, source_ref="recipe:summary"),
            ])
            next_summary_row += 1
            count_target = f"Summary!B{next_summary_row}"
            formulas.append(FormulaSpec(target=count_target, expression=f"=COUNT({reference})", result_type="integer"))
            summary_cells.extend([
                _text_cell(f"Summary!A{next_summary_row}", f"{column.label} count", "recipe:summary"),
                CellIR(address=count_target, data_type="null", value=None, source_ref="recipe:summary"),
            ])
            next_summary_row += 1
    for formula in parsed.formulas:
        formulas.append(formula)
        if not any(cell.address == formula.target for cell in summary_cells + detail_cells):
            summary_cells.append(CellIR(address=formula.target, data_type="null", value=None, source_ref=formula.source_ref))

    workbook = WorkbookIR(
        workbook_id=parsed.workbook_id,
        title=parsed.title,
        locale=parsed.locale,
        sheets=[
            SheetIR(name="Summary", cells=summary_cells, column_labels=["Metric", "Value"]),
            SheetIR(name="Detail", cells=detail_cells, column_labels=[column.label for column in table.columns]),
        ],
        formulas=formulas,
        named_ranges=named_ranges,
        source_lineage=source_lineage,
        assumptions={"model_calls": "0", "recipe": "workbook-builder-v1", "source_table": table.name},
    )
    evaluate_workbook(workbook)
    return workbook


__all__ = ["WorkbookBuildError", "build_workbook_ir"]
