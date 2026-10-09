from datetime import date

import pytest
from document_production.recipe import RequestValidationError, build_document_ir


def sample_request(**overrides):
    payload = {
        "template_id": "invoice",
        "template_version": "1",
        "document_id": "doc-001",
        "currency": "THB",
        "title": "Invoice",
        "customer_name": "Test customer",
        "issue_date": "2026-09-22",
        "line_items": [{"description": "Service", "quantity": "2", "unit_price": "10.00", "tax_rate": "7"}],
    }
    payload.update(overrides)
    return payload


def test_recipe_calculates_totals_without_model_calls():
    ir = build_document_ir(sample_request())
    assert ir.derived_totals == {"subtotal": "20.00", "tax_total": "1.40", "grand_total": "21.40"}
    assert ir.assumptions["calculation"] == "decimal-half-up-v1"
    assert ir.assumptions["model_calls"] == "0"
    assert ir.issue_date == date(2026, 9, 22)


def test_recipe_rounds_each_line_deterministically():
    request = sample_request(line_items=[{"description": "A", "quantity": "3", "unit_price": "0.335", "tax_rate": "0"}])
    ir = build_document_ir(request)
    assert ir.derived_totals["subtotal"] == "1.01"
    assert ir.sections[0].tables[0].rows[0].cells[-1] == "1.01"


def test_recipe_rejects_invalid_request_before_rendering():
    with pytest.raises(RequestValidationError, match="quantity_must_be_positive"):
        build_document_ir(sample_request(line_items=[{"description": "A", "quantity": "0", "unit_price": "1", "tax_rate": "0"}]))
