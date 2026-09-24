from book_production_platform.spreadsheet_ir import validate_request


def test_validate_request_reports_unknown_and_missing_columns() -> None:
    issues = validate_request({
        "workbook_id": "invalid-001",
        "tables": [{
            "name": "Detail",
            "columns": [{"key": "item", "label": "Item", "data_type": "text", "required": True}],
            "rows": [{"other": "x"}],
        }],
    })

    assert {issue.code for issue in issues} >= {"unknown_column", "required_value_missing"}


def test_validate_request_reports_mixed_typed_value() -> None:
    issues = validate_request({
        "workbook_id": "invalid-002",
        "tables": [{
            "name": "Detail",
            "columns": [{"key": "amount", "label": "Amount", "data_type": "decimal"}],
            "rows": [{"amount": "not-a-number"}],
        }],
    })

    assert any(issue.code == "invalid_typed_value" for issue in issues)
