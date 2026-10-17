"""Regressions from the gpt-6-luna shadow run (synthetic eval data only).

Each reply below is the raw model reply captured for that eval case in the
shadow run of 2026-10-03; briefs come from the fixture by case id. The fixes
are deterministic (grounding, allowlist, brief contract checks), so replaying
the captured reply proves them without any network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from brief_adapter_kit.testing import ScriptedTransport
from data_production.provider import (
    ProviderChartAdapter,
    _dataset_request,
    brief_contract_violation,
    ground_chart_proposal,
)
from data_production.recipe import build_dataset_ir

FIXTURES = Path(__file__).parent / "fixtures"
CASES = {case["id"]: case for case in json.loads((FIXTURES / "eval_dvz_brief30.json").read_text(encoding="utf-8"))}

# Raw replies captured in the shadow run (old prompt data-viz-brief.v1).
CAPTURED: dict[str, str] = {
    "c-en-04": (
        '{"chart":{"chart_type":"bar","x_column":"status","y_column":null,"aggregation":"count","value_unit":"tickets",'
        '"title":"Tickets by status"},"evidence":{"x_column":"by status","y_column":"Count of tickets","aggregation":"Count",'
        '"chart_type":"bar chart","value_unit":"tickets"},"provided":["chart_type","x_column","aggregation","value_unit"],'
        '"inferred":["title"],"missing":[]}'
    ),
    "c-en-05": (
        '{"chart":{"chart_type":"bar","x_column":"category","y_column":"score","aggregation":"avg","value_unit":null,'
        '"title":"Mean Score per Category"},"evidence":{"x_column":"per category","y_column":"score","aggregation":"mean",'
        '"chart_type":"Bar chart","value_unit":""},"provided":["chart_type","x_column","y_column","aggregation"],'
        '"inferred":["x_column","y_column","aggregation"],"missing":["value_unit"]}'
    ),
    "c-th-02": (
        '{"chart":{"chart_type":"line","x_column":"วันที่ | date","y_column":"จำนวนลูกค้า | integer","aggregation":"sum",'
        '"value_unit":"ลูกค้า","title":"จำนวนลูกค้ารวมตามวันที่"},"evidence":{"x_column":"ตามวันที่","y_column":"จำนวนลูกค้า",'
        '"aggregation":"รวม","chart_type":"กราฟเส้น","value_unit":"จำนวนลูกค้า"},'
        '"provided":["x_column","y_column","aggregation","chart_type","value_unit"],"inferred":[],"missing":[]}'
    ),
    "c-th-05": (
        '{"chart":{"chart_type":"line","x_column":"month","y_column":"orders","aggregation":"sum","value_unit":"orders",'
        '"title":"ยอด orders รวมรายเดือน"},"evidence":{"x_column":"(month)","y_column":"orders","aggregation":"รวม",'
        '"chart_type":"กราฟเส้น","value_unit":"ยอด orders"},"provided":["x_column","y_column","aggregation","chart_type","value_unit"],'
        '"inferred":[],"missing":[]}'
    ),
    "amb-th-01": (
        '{"chart":{"chart_type":"","x_column":"","y_column":"revenue","aggregation":"","value_unit":null,"title":"Revenue"},'
        '"evidence":{"x_column":"","y_column":"revenue","aggregation":"","chart_type":"","value_unit":""},'
        '"provided":["y_column"],"inferred":[],"missing":["x_column","aggregation","chart_type","value_unit"]}'
    ),
    "adv-th-01": (
        '{"chart":{"chart_type":"bar","x_column":"region","y_column":"amount_thb","aggregation":"sum","value_unit":"THB",'
        '"title":"ผลรวม amount_thb ตาม region"},"evidence":{"x_column":"ตาม region","y_column":"amount_thb",'
        '"aggregation":"รวม amount_thb","chart_type":"รวม amount_thb ตาม region","value_unit":"amount_thb"},'
        '"provided":["x_column","y_column","aggregation","value_unit"],"inferred":["chart_type"],"missing":[]}'
    ),
}
# adv-en-02 ("... as a pie chart") hit a gateway error in the captured run; an earlier run accepted a
# compliant-looking bar chart for it. This is that substitution: allowlisted type, grounded columns.
PIE_AS_BAR = {
    "chart": {"chart_type": "bar", "x_column": "region", "y_column": "amount_thb", "aggregation": "sum", "title": "Total by region"},
    "evidence": {"x_column": "by region", "y_column": "Total amount_thb", "aggregation": "Total amount_thb"},
    "provided": ["x_column", "y_column", "aggregation"], "inferred": ["chart_type"], "missing": [],
}


def _config() -> dict[str, Any]:
    return {
        "schema_version": "data-viz-provider-config.v1",
        "mode": "shadow",
        "gateway_url": "https://gateway.example.test",
        "token_env": "DVZ_TEST_GATEWAY_TOKEN",
        "rate_card": {"version": "test-card-1", "input_usd_per_1k": "0.001", "output_usd_per_1k": "0.002"},
        "caps": {"per_job_usd": "0.05", "per_day_usd": "1.00", "per_month_usd": "10.00"},
        "approval": {"approved_by": "test-owner", "approved_on": "2026-10-03"},
    }


def _replay(tmp_path: Path, case_id: str, reply: Any) -> tuple[Any, ScriptedTransport]:
    transport = ScriptedTransport(reply)
    adapter = ProviderChartAdapter(provider_config=_config(), ledger_root=tmp_path / "budget", transport=transport)
    return adapter.propose(CASES[case_id]["request"], job_id=f"replay-{case_id}"), transport


# --- safety: adversarial briefs are refused by code, before any spend ------------


def test_pie_request_is_rejected_even_when_the_model_substitutes_bar(tmp_path: Path) -> None:
    result, transport = _replay(tmp_path, "adv-en-02", PIE_AS_BAR)
    assert (result.status, result.issue_codes, result.model_calls) == ("rejected", ["chart_type_not_allowed"], 0)
    assert transport.payloads == []
    request = CASES["adv-en-02"]["request"]
    dataset = build_dataset_ir(_dataset_request(request))
    direct = ground_chart_proposal(PIE_AS_BAR, brief=request["brief"], dataset=dataset, envelope=request)
    assert (direct.status, direct.issue_codes) == ("rejected", ["chart_type_not_allowed"])


def test_request_for_computed_numbers_is_rejected_without_a_call(tmp_path: Path) -> None:
    result, transport = _replay(tmp_path, "adv-th-01", CAPTURED["adv-th-01"])
    assert (result.status, result.issue_codes, result.model_calls) == ("rejected", ["computed_value_forbidden"], 0)
    assert transport.payloads == [] and result.request is None


@pytest.mark.parametrize(
    "brief",
    [
        "Total sales by region as a donut chart.",
        "Scatter plot of price vs score.",
        "Horizontal bar chart of revenue by region.",
        "ขอกราฟวงกลมยอดขายตาม region",
        "Chart revenue by region and give me the exact numbers.",
        "What is the total revenue per region?",
        "คำนวณยอดรวมให้หน่อย แยกตาม region",
    ],
)
def test_brief_contract_violations_are_detected(brief: str) -> None:
    assert brief_contract_violation(brief) is not None


def test_contract_check_has_no_false_positive_on_non_adversarial_briefs() -> None:
    for case in CASES.values():
        brief = case["request"].get("brief")
        if brief and case["category"] != "adversarial":
            assert brief_contract_violation(brief) is None, case["id"]
    for brief in ("Give me the totals by region as a bar chart.", "ยอดขายตามพื้นที่ เป็นกราฟแท่ง", "Show total amount_thb by region"):
        assert brief_contract_violation(brief) is None, brief


# --- contract fixes: correct, grounded answers are no longer refused --------------


@pytest.mark.parametrize("case_id", ["c-en-04", "c-en-05", "c-th-02", "c-th-05"])
def test_captured_clear_replies_now_complete_with_the_expected_mapping(tmp_path: Path, case_id: str) -> None:
    result, _ = _replay(tmp_path, case_id, CAPTURED[case_id])
    want = CASES[case_id]["expected"]["chart"]
    assert result.status == "complete", result.issues
    assert {key: result.chart.get(key) for key in want} == want
    assert result.request is not None and result.model_calls == 1


def test_counted_noun_unit_is_dropped_not_kept(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "c-en-04", CAPTURED["c-en-04"])
    assert "value_unit" not in result.chart


def test_blank_strings_mean_not_proposed(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "amb-th-01", CAPTURED["amb-th-01"])
    assert result.status == "needs_input"
    assert set(CASES["amb-th-01"]["expected"]["missing"]) <= set(result.missing)
    assert "chart_type_not_allowed" not in result.issue_codes


# --- the fixes do not weaken grounding, allowlists, or unit checks ----------------


def _with(case_id: str, **chart: Any) -> dict[str, Any]:
    reply = json.loads(CAPTURED[case_id])
    reply["chart"].update(chart)
    return reply


def test_currency_unit_on_a_count_chart_still_blocks(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "c-en-04", _with("c-en-04", value_unit="THB"))
    assert result.status == "needs_input"


def test_unknown_unit_that_is_not_the_measure_name_stays_ungrounded(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "c-th-05", _with("c-th-05", value_unit="lbs"))
    assert result.status == "needs_input" and "ungrounded_field" in result.issue_codes


def test_header_with_the_wrong_type_suffix_is_not_a_header(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "c-th-02", _with("c-th-02", x_column="วันที่ | text"))
    assert result.status == "needs_input" and "unknown_column" in result.issue_codes


def test_contradictory_labels_still_need_a_grounded_quote(tmp_path: Path) -> None:
    reply = json.loads(CAPTURED["c-en-05"])
    reply["evidence"]["aggregation"] = "per category"  # names no aggregation
    result, _ = _replay(tmp_path, "c-en-05", reply)
    assert result.status == "needs_input" and "aggregation" in result.missing


def test_blank_strings_never_bypass_the_allowlist(tmp_path: Path) -> None:
    result, _ = _replay(tmp_path, "c-en-04", _with("c-en-04", chart_type="pie"))
    assert (result.status, result.issue_codes) == ("rejected", ["chart_type_not_allowed"])
