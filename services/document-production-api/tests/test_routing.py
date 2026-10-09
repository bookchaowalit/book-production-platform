from decimal import Decimal

from document_production.budget import BudgetLedger
from document_production.extraction import (
    BriefRequest,
    ExtractionResult,
    validate_extraction_result,
)
from document_production.routing import route_brief


def complete_brief() -> BriefRequest:
    return BriefRequest(
        document_id="brief-001",
        locale="en-US",
        brief="""title: March invoice
customer: Acme Co
date: 2026-09-22
currency: USD
item: Hosting | 2 | 100.50 | 7
""",
    )


def test_route_prefers_deterministic_parser_and_returns_typed_request():
    route = route_brief(complete_brief())

    assert route.status == "accepted"
    assert route.request is not None
    assert route.request.document_id == "brief-001"
    assert route.request.line_items[0].unit_price == Decimal("100.50")
    assert route.extraction.model_calls == 0
    assert route.extraction.adapter == "rule-based"


def test_route_abstains_with_missing_critical_fields():
    request = BriefRequest(document_id="brief-002", brief="title: Missing fields")

    route = route_brief(request)

    assert route.status == "needs_input"
    assert route.request is None
    assert "customer_name" in route.extraction.missing
    assert any(issue["code"] == "missing_required_field" for issue in route.issues)


class UnsafeAdapter:
    def extract(self, brief: str, *, locale: str, template_id: str = "invoice", template_version: str = "1", budget=None) -> ExtractionResult:
        del brief, locale, template_id, template_version, budget
        return ExtractionResult(
            status="complete",
            fields={
                "title": "Invoice",
                "customer_name": "Acme Co",
                "issue_date": "2026-09-22",
                "currency": "USD",
                "line_items": [{"description": "Service", "quantity": "1", "unit_price": "10", "tax_rate": "0"}],
                "grand_total": "10.00",
            },
            provided=["title", "customer_name", "issue_date", "currency", "line_items"],
            model_calls=1,
            adapter="unsafe-test",
            input_tokens=20,
            output_tokens=40,
        )


def test_route_rejects_model_derived_totals_before_rendering(tmp_path):
    request = BriefRequest(document_id="brief-003", brief="title: Need adapter")

    route = route_brief(request, adapter=UnsafeAdapter(), budget=BudgetLedger(tmp_path))

    assert route.status == "rejected"
    assert route.request is None
    assert any(issue["code"] == "derived_total_forbidden" for issue in route.issues)


def test_extraction_requires_provenance_for_every_field():
    result = ExtractionResult(
        status="complete",
        fields={"title": "Invoice"},
        provided=[],
        model_calls=1,
        adapter="test",
    )

    issues = validate_extraction_result(result)

    assert any(issue["code"] == "missing_field_provenance" for issue in issues)


class CompleteAdapter:
    def extract(self, brief: str, *, locale: str, template_id: str = "invoice", template_version: str = "1", budget=None) -> ExtractionResult:
        del brief, locale, template_id, template_version, budget
        return ExtractionResult(
            status="complete",
            fields={
                "title": "Invoice",
                "customer_name": "Acme",
                "issue_date": "2026-09-22",
                "currency": "USD",
                "line_items": [{"description": "Service", "quantity": "1", "unit_price": "10", "tax_rate": "0"}],
            },
            provided=["title", "customer_name", "issue_date", "currency", "line_items"],
            model_calls=1,
            adapter="complete-test",
            input_tokens=10,
            output_tokens=20,
        )


def test_route_budget_is_scoped_to_job_id_not_document_id(tmp_path):
    request = BriefRequest(document_id="same-document", brief="title: Need adapter")
    ledger = BudgetLedger(tmp_path)

    first = route_brief(request, adapter=CompleteAdapter(), budget=ledger, budget_job_id="job-a")
    second = route_brief(request, adapter=CompleteAdapter(), budget=ledger, budget_job_id="job-b")

    assert first.status == "accepted"
    assert second.status == "accepted"
