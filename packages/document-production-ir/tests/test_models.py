from datetime import date
from decimal import Decimal

from document_ir import DocumentRequest

SAMPLE_REQUEST = {
    "template_id": "invoice",
    "template_version": "1",
    "document_id": "doc-001",
    "currency": "THB",
    "title": "ใบเสนอราคา",
    "customer_name": "ลูกค้าทดสอบ",
    "issue_date": "2026-09-22",
    "line_items": [{"description": "บริการ", "quantity": "2", "unit_price": "10.00", "tax_rate": "7"}],
}


def test_request_round_trips_decimal_and_date():
    request = DocumentRequest.model_validate(SAMPLE_REQUEST)
    assert request.line_items[0].quantity == Decimal(2)
    assert request.issue_date == date(2026, 9, 22)
