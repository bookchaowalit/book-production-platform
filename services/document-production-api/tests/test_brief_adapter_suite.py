"""T8.1 60-case suite and T8.2 invariants for the brief-adapter.v1 flow.

Composition (roadmap T8.1): structured 20, clear-brief 20, ambiguous-brief 10,
adversarial 10 — half Thai, half English in every category. All cases are
synthetic fixtures. Invariants enforced here:

- totals in output always equal the recipe computation from the extracted
  fields (the adapter layer never computes money);
- ambiguous briefs abstain with ``needs_input`` and the exact missing-field
  list, never guessed values;
- adversarial briefs never produce an accepted document silently and end in
  ``needs_input`` or a stable rejection with bounded diagnostics;
- no telemetry, manifest, or log line ever contains the raw brief text.
"""

from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
from fake_adapter import ScriptedBriefAdapter

from document_production.budget import BudgetLedger
from document_production.extraction import (
    REQUIRED_FIELDS,
    BriefRequest,
    ExtractionResult,
)
from document_production.jobs import run_document_brief_job, run_document_job
from document_production.recipe import build_document_ir
from document_production.routing import route_brief

FIXTURES = Path(__file__).parent / "fixtures"
CENT = Decimal("0.01")


def _load_suite_cases() -> list[dict[str, Any]]:
    raw_cases = json.loads((FIXTURES / "adapter_suite_cases.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for raw in raw_cases:
        request = dict(raw["request"])
        if "brief_repeat" in raw:
            request["brief"] = str(request["brief"]) * int(raw["brief_repeat"])
        cases.append({**raw, "request": request})
    return cases


def _category(category: str) -> list[dict[str, Any]]:
    return [case for case in SUITE_CASES if case["category"] == category]


SUITE_CASES = _load_suite_cases()
CLEAR_CASES = _category("clear")
AMBIGUOUS_CASES = _category("ambiguous")
ADVERSARIAL_CASES = _category("adversarial")
STRUCTURED_CASES: list[dict[str, Any]] = [
    {**case, "request": dict(case["request"])}
    for case in json.loads((FIXTURES / "structured_cases.json").read_text(encoding="utf-8"))
    if case["expected"] == "accepted"
]


def _expected_totals(line_items: list[dict[str, Any]]) -> dict[str, str]:
    """Compute totals independently from extracted fields using the pinned formula."""

    subtotal = Decimal(0)
    tax_total = Decimal(0)
    for item in line_items:
        line = (Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"]))).quantize(CENT, rounding=ROUND_HALF_UP)
        tax = (line * Decimal(str(item.get("tax_rate", "0"))) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)
        subtotal += line
        tax_total += tax
    subtotal = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
    tax_total = tax_total.quantize(CENT, rounding=ROUND_HALF_UP)
    grand_total = (subtotal + tax_total).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"subtotal": f"{subtotal:.2f}", "tax_total": f"{tax_total:.2f}", "grand_total": f"{grand_total:.2f}"}


def _locale_split(cases: list[dict[str, Any]]) -> tuple[int, int]:
    locales = [case.get("locale") or case["request"]["locale"] for case in cases]
    return locales.count("en-US"), locales.count("th-TH")


def test_suite_composition_matches_roadmap_split():
    assert len(STRUCTURED_CASES) == 20
    assert _locale_split(STRUCTURED_CASES) == (10, 10)
    assert len(CLEAR_CASES) == 20
    assert _locale_split(CLEAR_CASES) == (10, 10)
    assert len(AMBIGUOUS_CASES) == 10
    assert _locale_split(AMBIGUOUS_CASES) == (5, 5)
    assert len(ADVERSARIAL_CASES) == 10
    assert _locale_split(ADVERSARIAL_CASES) == (5, 5)
    assert len(STRUCTURED_CASES) + len(SUITE_CASES) == 60


@pytest.mark.parametrize("case", STRUCTURED_CASES, ids=lambda case: case["id"])
def test_structured_case_renders_with_recipe_totals(case, tmp_path):
    result = run_document_job(dict(case["request"]), tmp_path, f"suite-{case['request']['document_id']}")

    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.manifest_path is not None
    assert build_document_ir(case["request"]).derived_totals == _expected_totals(case["request"]["line_items"])


@pytest.mark.parametrize("case", CLEAR_CASES, ids=lambda case: case["id"])
def test_clear_brief_case_accepted_with_recipe_totals(case, tmp_path):
    result = run_document_brief_job(dict(case["request"]), tmp_path, f"suite-{case['id']}")

    assert result.status == "accepted"
    assert result.model_calls == 0
    assert result.metadata["extraction"]["fields"] == case["expected_fields"]
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["qa"]["status"] == "passed"
    route = route_brief(BriefRequest.model_validate(case["request"]))
    assert route.request is not None
    assert build_document_ir(route.request).derived_totals == _expected_totals(case["expected_fields"]["line_items"])


@pytest.mark.parametrize("case", AMBIGUOUS_CASES, ids=lambda case: case["id"])
def test_ambiguous_brief_case_abstains_with_exact_missing_list(case, tmp_path):
    adapter = ScriptedBriefAdapter()
    adapter.register(
        case["request"]["brief"],
        ExtractionResult(
            status="needs_input",
            adapter=adapter.name,
            missing=list(case["expected_missing"]),
            model_calls=1,
            input_tokens=16,
        ),
    )

    result = run_document_brief_job(dict(case["request"]), tmp_path, f"suite-{case['id']}", adapter=adapter)

    assert result.status == "needs_input"
    assert result.error_code == "needs_input"
    assert result.model_calls == 1
    assert result.manifest_path is None
    assert not list(tmp_path.rglob("manifest.json"))
    assert result.metadata["extraction"]["missing"] == case["expected_missing"]
    fields = result.metadata["extraction"]["fields"]
    for missing_field in case["expected_missing"]:
        assert missing_field not in fields


@pytest.mark.parametrize("case", ADVERSARIAL_CASES, ids=lambda case: case["id"])
def test_adversarial_brief_case_never_silently_accepted(case, tmp_path):
    adapter = ScriptedBriefAdapter()
    adapter.register(
        case["request"]["brief"],
        ExtractionResult(
            status="needs_input",
            adapter=adapter.name,
            missing=list(REQUIRED_FIELDS),
            model_calls=1,
            input_tokens=16,
        ),
    )

    result = run_document_brief_job(dict(case["request"]), tmp_path, f"suite-{case['id']}", adapter=adapter)

    assert result.status in {"needs_input", "rejected"}
    assert result.status == case["expected"]
    assert result.manifest_path is None
    issues = result.qa.get("issues", [])
    assert len(issues) <= 16
    assert all(len(issue.get("message", "")) <= 300 for issue in issues)


def test_no_raw_brief_text_reaches_telemetry_manifests_or_logs(tmp_path, caplog):
    outputs: list[str] = []
    with caplog.at_level(logging.DEBUG):
        for case in SUITE_CASES:
            result = run_document_brief_job(
                dict(case["request"]),
                tmp_path / case["id"],
                f"suite-{case['id']}",
                adapter=ScriptedBriefAdapter(),
            )
            outputs.append(json.dumps(result.to_dict(), ensure_ascii=False, default=str))
            if result.manifest_path is not None:
                outputs.append(result.manifest_path.read_text(encoding="utf-8"))
    outputs.extend(record.getMessage() for record in caplog.records)
    blob = "\n".join(outputs)

    for case in SUITE_CASES:
        assert case["canary"] not in blob
        assert case["request"]["brief"] not in blob


def test_adapter_cannot_introduce_totals_or_unknown_fields(tmp_path):
    brief = "title: Sneaky totals"
    adapter = ScriptedBriefAdapter()
    adapter.register(
        brief,
        ExtractionResult(
            status="complete",
            fields={
                "title": "Sneaky totals",
                "customer_name": "Sneaky Co",
                "issue_date": "2026-09-22",
                "currency": "USD",
                "line_items": [{"description": "Service", "quantity": "1", "unit_price": "10.00", "tax_rate": "0"}],
                "grand_total": "10.70",
            },
            provided=["title", "customer_name", "issue_date", "currency", "line_items"],
            model_calls=1,
            adapter=adapter.name,
            input_tokens=16,
            output_tokens=64,
        ),
    )

    route = route_brief(BriefRequest(document_id="sneaky-totals", brief=brief), adapter=adapter, budget=BudgetLedger(tmp_path))

    assert route.status == "rejected"
    assert any(issue["code"] == "derived_total_forbidden" for issue in route.issues)


def test_adapter_cannot_introduce_unknown_fields(tmp_path):
    brief = "title: Sneaky fields"
    adapter = ScriptedBriefAdapter()
    adapter.register(
        brief,
        ExtractionResult(
            status="complete",
            fields={"title": "Sneaky fields", "internal_score": "0.99"},
            provided=["title", "internal_score"],
            model_calls=1,
            adapter=adapter.name,
            input_tokens=8,
        ),
    )

    route = route_brief(BriefRequest(document_id="sneaky-fields", brief=brief), adapter=adapter, budget=BudgetLedger(tmp_path))

    assert route.status == "rejected"
    assert any(issue["code"] == "unknown_extraction_field" for issue in route.issues)


def test_adapter_result_with_unsupported_version_is_rejected(tmp_path):
    brief = "title: Version probe"
    adapter = ScriptedBriefAdapter()
    adapter.register(
        brief,
        ExtractionResult(
            status="complete",
            fields={"title": "Version probe"},
            provided=["title"],
            model_calls=1,
            adapter=adapter.name,
            adapter_version="brief-adapter.v0",
            input_tokens=8,
        ),
    )

    route = route_brief(BriefRequest(document_id="version-probe", brief=brief), adapter=adapter, budget=BudgetLedger(tmp_path))

    assert route.status == "rejected"
    assert any(issue["code"] == "adapter_version_unsupported" for issue in route.issues)


def test_scripted_fake_adapter_abstains_on_unscripted_brief():
    result = ScriptedBriefAdapter().extract("some unscripted brief text", locale="en-US")

    assert result.status == "needs_input"
    assert result.fields == {}
    assert result.missing == list(REQUIRED_FIELDS)
    assert result.model_calls == 1
