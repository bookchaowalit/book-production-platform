"""Compile typed tables into the deterministic WorkbookIR recipe."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from book_production_platform.spreadsheet_ir import (
    DEFAULT_NUMBER_FORMATS,
    CellIR,
    ColumnFormatIR,
    ConditionalFormatIR,
    DataValidationIR,
    FormulaSpec,
    SheetIR,
    TableRequest,
    ValidationIssue,
    WorkbookIR,
    WorkbookRequest,
    parse_typed_value,
    validate_request,
)

from .formulas import INTENT_FORMULA_LANGUAGE, evaluate_workbook


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


RECIPE_ID = "workbook-builder-v2"
_RESERVED_SHEETS = {"summary"}


def _sheet_names(parsed: WorkbookRequest) -> list[str]:
    """The first table keeps the historical "Detail" sheet; later tables use their own names."""

    names = ["Detail", *(table.name for table in parsed.tables[1:])]
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for index, name in enumerate(names):
        folded = name.lower()
        if folded in _RESERVED_SHEETS or folded in seen or len(name) > 31:
            issues.append(ValidationIssue(
                code="sheet_name_conflict", path=f"tables[{index}].name",
                message="table names must be unique, at most 31 characters, and not Summary or Detail",
            ))
        seen.add(folded)
    if issues:
        raise WorkbookBuildError(issues)
    return names


def _intent_assumptions(parsed: WorkbookRequest) -> dict[str, str]:
    """Opt into the intent formula language only when the V1B intent compiler asked for it."""

    language = parsed.template_parameters.get("formula_language")
    if language is None:
        return {}
    if language != INTENT_FORMULA_LANGUAGE:
        raise WorkbookBuildError([ValidationIssue(
            code="formula_language_unsupported", path="template_parameters.formula_language",
            message="only the spreadsheet intent formula language may be selected",
        )])
    template_id = parsed.template_parameters.get("template_id")
    return {"formula_language": INTENT_FORMULA_LANGUAGE, **({"template_id": str(template_id)} if template_id else {})}


def _column_features(parsed: WorkbookRequest, table: TableRequest, data_start: int, data_end: int) -> dict[str, Any]:
    """Number formats, dropdown validations and conditional formats for one data sheet."""

    formats: list[ColumnFormatIR] = []
    validations: list[DataValidationIR] = []
    rules: list[ConditionalFormatIR] = []
    letters = {column.key: _column_name(index) for index, column in enumerate(table.columns, start=1)}
    for column in table.columns:
        letter = letters[column.key]
        if table.rows and column.data_type in DEFAULT_NUMBER_FORMATS:
            number_format = column.number_format or DEFAULT_NUMBER_FORMATS[column.data_type]
            formats.append(ColumnFormatIR(range=f"{letter}{data_start}:{letter}{data_end}", number_format=number_format))
        if column.allowed_values is not None:
            end = max(data_start, data_end)
            validations.append(DataValidationIR(range=f"{letter}{data_start}:{letter}{end}", allowed_values=list(column.allowed_values)))
    for rule in parsed.conditional_formats:
        if rule.table != table.name or not table.rows:
            continue
        letter = letters[rule.column]
        rules.append(ConditionalFormatIR(
            range=f"{letter}{data_start}:{letter}{data_end}",
            operator=rule.operator, value=rule.value, value2=rule.value2, fill_color=rule.fill_color.upper(),
        ))
    return {"column_formats": formats, "data_validations": validations, "conditional_formats": rules}


def build_workbook_ir(request: WorkbookRequest | dict[str, Any]) -> WorkbookIR:
    """Build a Summary sheet plus one data sheet per table and validate all formulas before export."""

    parsed = request if isinstance(request, WorkbookRequest) else WorkbookRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise WorkbookBuildError(issues)
    sheet_names = _sheet_names(parsed)
    multi = len(parsed.tables) > 1
    summary_cells: list[CellIR] = [
        _text_cell("Summary!A1", parsed.title, "recipe:title"),
        _text_cell("Summary!A3", "Metric", "recipe:header"),
        _text_cell("Summary!B3", "Value", "recipe:header"),
    ]
    data_sheets: list[SheetIR] = []
    formulas: list[FormulaSpec] = []
    named_ranges: dict[str, str] = {}
    source_lineage: dict[str, str] = {}
    next_summary_row = 4
    data_start = 2
    for table, sheet in zip(parsed.tables, sheet_names):
        cells: list[CellIR] = []
        for column_index, column in enumerate(table.columns, start=1):
            address = f"{sheet}!{_column_name(column_index)}1"
            cells.append(_text_cell(address, column.label, f"{table.source_ref}:column:{column.key}"))
            source_lineage[address] = f"{table.source_ref}:column:{column.key}"
        for row_index, row in enumerate(table.rows, start=data_start):
            for column_index, column in enumerate(table.columns, start=1):
                address = f"{sheet}!{_column_name(column_index)}{row_index}"
                value = parse_typed_value(row.get(column.key), column.data_type, path=address)
                cells.append(CellIR(address=address, data_type=_cell_type(value), value=value, source_ref=table.source_ref))
                source_lineage[address] = table.source_ref
        data_end = data_start + len(table.rows) - 1
        data_sheets.append(SheetIR(
            name=sheet,
            cells=cells,
            column_labels=[column.label for column in table.columns],
            **_column_features(parsed, table, data_start, data_end),
        ))
        if not table.rows:
            continue
        for column_index, column in enumerate(table.columns, start=1):
            if column.data_type not in {"integer", "decimal"}:
                continue
            column_letter = _column_name(column_index)
            reference = f"{sheet}!{column_letter}{data_start}:{column_letter}{data_end}"
            named_ranges[f"{sheet}_{column.key}"] = reference
            label = f"{table.name}: {column.label}" if multi else column.label
            sum_target = f"Summary!B{next_summary_row}"
            result_type: Literal["integer", "decimal"] = "integer" if column.data_type == "integer" else "decimal"
            formulas.append(FormulaSpec(target=sum_target, expression=f"=SUM({reference})", result_type=result_type))
            summary_cells.extend([
                _text_cell(f"Summary!A{next_summary_row}", label, "recipe:summary"),
                CellIR(address=sum_target, data_type="null", value=None, source_ref="recipe:summary"),
            ])
            next_summary_row += 1
            count_target = f"Summary!B{next_summary_row}"
            formulas.append(FormulaSpec(target=count_target, expression=f"=COUNT({reference})", result_type="integer"))
            summary_cells.extend([
                _text_cell(f"Summary!A{next_summary_row}", f"{label} count", "recipe:summary"),
                CellIR(address=count_target, data_type="null", value=None, source_ref="recipe:summary"),
            ])
            next_summary_row += 1
    all_cells = summary_cells + [cell for sheet in data_sheets for cell in sheet.cells]
    for formula in parsed.formulas:
        formulas.append(formula)
        if not any(cell.address == formula.target for cell in all_cells):
            summary_cells.append(CellIR(address=formula.target, data_type="null", value=None, source_ref=formula.source_ref))

    workbook = WorkbookIR(
        workbook_id=parsed.workbook_id,
        title=parsed.title,
        locale=parsed.locale,
        sheets=[SheetIR(name="Summary", cells=summary_cells, column_labels=["Metric", "Value"]), *data_sheets],
        formulas=formulas,
        named_ranges=named_ranges,
        source_lineage=source_lineage,
        assumptions={
            "model_calls": "0", "recipe": RECIPE_ID,
            "source_table": parsed.tables[0].name, "source_tables": ",".join(table.name for table in parsed.tables),
            **_intent_assumptions(parsed),
        },
    )
    evaluate_workbook(workbook)
    return workbook


__all__ = ["RECIPE_ID", "WorkbookBuildError", "build_workbook_ir"]
