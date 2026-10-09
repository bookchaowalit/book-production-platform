from document_production.extraction import RuleBasedExtractor


def test_rule_parser_extracts_complete_english_labeled_brief():
    brief = """title: March invoice
customer: Acme Co
date: 2026-09-22
currency: USD
item: Hosting | 2 | 100.50 | 7
notes: Due in 30 days
"""

    result = RuleBasedExtractor().extract(brief, locale="en-US")

    assert result.status == "complete"
    assert result.fields["title"] == "March invoice"
    assert result.fields["customer_name"] == "Acme Co"
    assert result.fields["issue_date"] == "2026-09-22"
    assert result.fields["currency"] == "USD"
    assert result.fields["line_items"] == [{"description": "Hosting", "quantity": "2", "unit_price": "100.50", "tax_rate": "7"}]
    assert result.fields["notes"] == "Due in 30 days"
    assert result.provided == ["title", "customer_name", "issue_date", "currency", "line_items", "notes"]
    assert result.inferred == []
    assert result.missing == []
    assert result.model_calls == 0


def test_rule_parser_handles_thai_labels_and_decimal_comma():
    brief = """หัวข้อ: ใบแจ้งหนี้
ลูกค้า: บริษัท ตัวอย่าง จำกัด
วันที่: 22/09/2026
สกุลเงิน: บาท
รายการ: บริการออกแบบ | 1 | 1,250.50 | 7
"""

    result = RuleBasedExtractor().extract(brief, locale="th-TH")

    assert result.status == "complete"
    assert result.fields["customer_name"] == "บริษัท ตัวอย่าง จำกัด"
    assert result.fields["issue_date"] == "2026-09-22"
    assert result.fields["currency"] == "THB"
    assert result.fields["line_items"][0]["unit_price"] == "1250.50"
    assert result.missing == []


def test_rule_parser_abstains_with_actionable_missing_fields():
    result = RuleBasedExtractor().extract(
        "title: Draft\nitem: Service | two | 100 | 7\n",
        locale="en-US",
    )

    assert result.status == "needs_input"
    assert "customer_name" in result.missing
    assert "issue_date" in result.missing
    assert "currency" in result.missing
    assert any(issue["code"] == "line_item_invalid" for issue in result.issues)
    assert result.provided == ["title"]


def test_rule_parser_rejects_briefs_over_input_token_cap():
    result = RuleBasedExtractor().extract("word " * 8001, locale="en-US")

    assert result.status == "rejected"
    assert any(issue["code"] == "input_token_limit_exceeded" for issue in result.issues)
