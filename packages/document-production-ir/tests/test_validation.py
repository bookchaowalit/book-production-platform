from copy import deepcopy

from document_ir import validate_request
from test_models import SAMPLE_REQUEST


def test_validation_rejects_empty_description_and_zero_quantity():
    request = deepcopy(SAMPLE_REQUEST)
    request["line_items"] = [{"description": "", "quantity": "0", "unit_price": "10.00", "tax_rate": "7"}]
    issues = validate_request(request)
    assert {issue.code for issue in issues} == {"line_item_description_required", "quantity_must_be_positive"}


def test_validation_rejects_101_line_items_without_truncating():
    request = deepcopy(SAMPLE_REQUEST)
    request["line_items"] = request["line_items"] * 101
    original_count = len(request["line_items"])
    issues = validate_request(request)
    assert any(issue.code == "line_item_limit_exceeded" for issue in issues)
    assert len(request["line_items"]) == original_count


def test_validation_rejects_unsupported_currency():
    request = deepcopy(SAMPLE_REQUEST)
    request["currency"] = "JPY"
    assert any(issue.code == "unsupported_currency" for issue in validate_request(request))
