from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from book_production_platform.spreadsheet_ir import WorkbookRequest, parse_typed_value


def request() -> dict[str, object]:
    return {
        "workbook_id": "sales-001",
        "title": "ยอดขาย",
        "locale": "th-TH",
        "tables": [{
            "name": "Detail",
            "columns": [
                {"key": "item", "label": "รายการ", "data_type": "text", "required": True},
                {"key": "amount", "label": "ยอดรวม", "data_type": "decimal", "required": True},
                {"key": "date", "label": "วันที่", "data_type": "date"},
            ],
            "rows": [{"item": "=SUM(A1:A2)", "amount": "10.50", "date": "2026-09-22"}],
        }],
    }


def test_workbook_request_preserves_formula_like_text_as_text() -> None:
    parsed = WorkbookRequest.model_validate(request())

    assert parsed.tables[0].rows[0]["item"] == "=SUM(A1:A2)"
    assert parse_typed_value(parsed.tables[0].rows[0]["item"], "text") == "=SUM(A1:A2)"
    assert parse_typed_value("10.50", "decimal") == Decimal("10.50")
    assert parse_typed_value("2026-09-22", "date") == date(2026, 9, 22)


def test_column_key_rejects_excel_formula_syntax() -> None:
    bad = request()
    table = bad["tables"]
    assert isinstance(table, list)
    columns = table[0]["columns"]
    assert isinstance(columns, list)
    columns[0]["key"] = "=SUM"

    with pytest.raises(ValidationError):
        WorkbookRequest.model_validate(bad)
