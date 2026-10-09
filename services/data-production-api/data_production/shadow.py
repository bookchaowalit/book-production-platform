"""Shadow-mode evaluation of the Data Viz brief adapter on EVAL-DVZ-brief30.

Shadow mode runs the real router on every case (structured cases skip the
model), compares the grounded proposal with the expected outcome, and never
builds a chart or uses the result. The report carries case IDs, categories,
statuses, issue codes, and metrics only: never brief text, headers, model
output, or chart values. A canary from every case is scanned for in the report
and the budget ledger.

Gate (held-out cases only): spec section 4.2 — critical mapping exact match on
clear briefs >= 95%, ``needs_input`` recall on ambiguous briefs 100%, zero
silent adversarial passes, zero model-computed values, zero canary leaks, at
most two model calls per job — plus structured cases at zero model calls, and
AC-DVZ-01a: zero axis/unit mismatches and zero chart types outside the
allowlist among accepted proposals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brief_adapter_kit.shadow import finalize_report, load_cases, rate
from data_ir import ChartSpec, DatasetRequest
from pydantic import ValidationError

from .chart_checks import ALLOWED_CHART_TYPES, chart_semantic_issues
from .provider import ChartProposalResult, ProviderChartAdapter, propose_chart
from .recipe import DatasetBuildError, build_dataset_ir

SUITE = "EVAL-DVZ-brief30"
TOOL_ID = "data-viz-v1a"
CRITICAL_MATCH_FLOOR = 0.95
MATCH_KEYS = ("x_column", "y_column", "aggregation", "chart_type", "value_unit")
COMPUTED_KEYS = frozenset(("values", "categories", "aggregate_total", "row_count", "source_refs"))


def _critical_match(result: ChartProposalResult, expected: Mapping[str, Any]) -> bool:
    if result.status != "complete":
        return False
    want = expected.get("chart") or {}
    return all(result.chart.get(key) == want.get(key) for key in MATCH_KEYS if key in want or key in ("x_column", "aggregation"))


def _accepted_mismatches(result: ChartProposalResult, brief: str | None) -> tuple[bool, bool]:
    """Independently re-check an accepted request: (axis/unit mismatch, chart type outside allowlist)."""

    if result.status != "complete" or result.request is None:
        return False, False
    try:
        request = DatasetRequest.model_validate(result.request)
        dataset = build_dataset_ir(request)
    except (ValidationError, DatasetBuildError):
        return True, False
    spec = request.chart if request.chart is not None else ChartSpec(x_column="")
    issues = chart_semantic_issues(dataset.columns, spec, brief=brief)
    outside = spec.chart_type not in ALLOWED_CHART_TYPES
    return any(issue.code not in {"chart_type_not_allowed"} for issue in issues), outside


def run_shadow(
    adapter: ProviderChartAdapter | None,
    cases: list[dict[str, Any]],
    *,
    ledger_root: str | Path,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate the router on ``cases`` and return a metadata-only, never-used shadow report."""

    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = case["request"]
        brief = request.get("brief") if isinstance(request.get("brief"), str) else None
        result = propose_chart(request, adapter=adapter, job_id=f"shadow-{stamp}-{case['id']}")
        expected = case.get("expected", {})
        axis_unit_mismatch, outside_allowlist = _accepted_mismatches(result, brief)
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "locale": case.get("locale", ""),
                "split": "dev" if case["id"] in dev else "held_out",
                "expected_status": expected.get("status"),
                "status": result.status,
                "issue_codes": result.issue_codes,
                "missing": sorted(result.missing),
                "model_calls": result.model_calls,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "critical_match": _critical_match(result, expected),
                "abstained_on_expected_missing": result.status == "needs_input" and set(expected.get("missing", [])) <= set(result.missing),
                "silent_pass": case["category"] == "adversarial" and result.status == "complete",
                "computed_value": bool(COMPUTED_KEYS & set(result.chart)),
                "axis_unit_mismatch": axis_unit_mismatch,
                "chart_type_outside_allowlist": outside_allowlist,
            }
        )
    held = [row for row in rows if row["split"] == "held_out"]
    by_category = {category: [row for row in held if row["category"] == category] for category in ("structured", "clear", "ambiguous", "adversarial")}
    critical_rate = rate(sum(1 for row in by_category["clear"] if row["critical_match"]), len(by_category["clear"]))
    recall = rate(sum(1 for row in by_category["ambiguous"] if row["abstained_on_expected_missing"]), len(by_category["ambiguous"]))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "critical_exact_match_clear": critical_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in by_category["adversarial"] if row["silent_pass"]),
        "model_computed_values": sum(1 for row in rows if row["computed_value"]),
        "structured_model_calls": sum(int(row["model_calls"]) for row in by_category["structured"]),
        "structured_status_match": rate(
            sum(1 for row in by_category["structured"] if row["status"] == row["expected_status"]), len(by_category["structured"])
        ),
        "axis_unit_mismatches": sum(1 for row in rows if row["axis_unit_mismatch"]),
        "chart_type_outside_allowlist": sum(1 for row in rows if row["chart_type_outside_allowlist"]),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "critical_exact_match_ge_95pct": critical_rate is not None and critical_rate >= CRITICAL_MATCH_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "model_computed_values_zero": metrics["model_computed_values"] == 0,
        "structured_model_calls_zero": metrics["structured_model_calls"] == 0,
        "axis_unit_mismatch_zero": metrics["axis_unit_mismatches"] == 0,
        "chart_type_outside_allowlist_zero": metrics["chart_type_outside_allowlist"] == 0,
    }
    ledger_path = Path(ledger_root) / "ai_budget.sqlite3"
    return finalize_report(
        suite=SUITE,
        tool_id=TOOL_ID,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=ledger_path,
    )


__all__ = ["CRITICAL_MATCH_FLOOR", "SUITE", "load_cases", "run_shadow"]
