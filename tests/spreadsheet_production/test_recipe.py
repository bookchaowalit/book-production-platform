import pytest

from book_production_platform.spreadsheet_ir import WorkbookRequest
from book_production_platform.spreadsheet_production.formulas import FormulaError
from book_production_platform.spreadsheet_production.recipe import (
    WorkbookBuildError,
    build_workbook_ir,
)


def request() -> dict[str, object]:
    return {
        "workbook_id": "sales-001",
        "title": "สรุปยอดขาย",
        "locale": "th-TH",
        "tables": [{
            "name": "Orders",
            "source_ref": "fixture:orders",
            "columns": [
                {"key": "item", "label": "รายการ", "data_type": "text", "required": True},
                {"key": "amount", "label": "ยอดรวม", "data_type": "decimal", "required": True},
                {"key": "count", "label": "จำนวน", "data_type": "integer"},
                {"key": "sold_on", "label": "วันที่", "data_type": "date"},
            ],
            "rows": [
                {"item": "=SUM(A1:A2)", "amount": "10.50", "count": 2, "sold_on": "2026-09-22"},
                {"item": "สินค้า B", "amount": "5.25", "count": 1, "sold_on": "2026-09-23"},
            ],
        }],
    }


def test_build_workbook_ir_creates_summary_detail_and_named_ranges() -> None:
    ir = build_workbook_ir(request())

    assert [sheet.name for sheet in ir.sheets] == ["Summary", "Detail"]
    assert ir.named_ranges["Detail_amount"] == "Detail!B2:B3"
    assert ir.source_lineage["Detail!A2"] == "fixture:orders"
    assert any(formula.expression == "=SUM(Detail!B2:B3)" for formula in ir.formulas)
    assert next(cell for cell in ir.sheets[1].cells if cell.address == "Detail!A2").value == "=SUM(A1:A2)"


def test_build_workbook_ir_computes_decimal_totals_without_model_calls() -> None:
    ir = build_workbook_ir(WorkbookRequest.model_validate(request()))

    assert ir.assumptions["model_calls"] == "0"
    total = next(formula for formula in ir.formulas if formula.target == "Summary!B4")
    assert total.result_type == "decimal"


def test_build_workbook_ir_rejects_invalid_input_and_formula() -> None:
    bad = request()
    table = bad["tables"]
    assert isinstance(table, list)
    table[0]["rows"] = [{"item": "x", "amount": "not-a-number"}]

    with pytest.raises(WorkbookBuildError, match="invalid_typed_value"):
        build_workbook_ir(bad)

    with pytest.raises(FormulaError, match="broken_reference"):
        build_workbook_ir({**request(), "formulas": [{"target": "Summary!B20", "expression": "=SUM(Detail!Z99:Z100)"}]})
