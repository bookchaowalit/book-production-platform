"""Shadow-mode evaluation of a brief adapter against the EVAL-DOC-brief60 suite (T3.4).

Shadow mode calls the adapter for every free-text case, compares its proposal
with the expected outcome, and never renders a document or uses the result.
The report contains case IDs, categories, statuses, and metrics only: it never
contains brief text, model output, or field values, and it fails the
privacy check if any case canary leaks into it or into the budget ledger.

Gate thresholds follow the shared spec section 4.2: critical-field exact match
on clear briefs >= 95%, ``needs_input`` recall on ambiguous briefs 100%, zero
silent adversarial passes, zero model-computed totals, zero canary leaks, and
at most two model calls per job. Only held-out cases decide the gate.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from brief_adapter_kit.shadow import ledger_contains

from .budget import BudgetExceeded, BudgetLedger
from .extraction import ExtractionAdapter, ExtractionResult, validate_extraction_result

SHADOW_REPORT_SCHEMA = "documents-shadow-report.v1"
CRITICAL_MATCH_FLOOR = 0.95
NUMERIC_KEYS = ("quantity", "unit_price", "tax_rate")


def _same_description(got: str, want: str) -> bool:
    """Free-text line descriptions match when one normalized form contains the other."""

    a, b = " ".join(got.casefold().split()), " ".join(want.casefold().split())
    return bool(a and b) and (a in b or b in a)


def _same_items(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
        return False
    for got, want in zip(actual, expected, strict=True):
        if not isinstance(got, dict) or not _same_description(str(got.get("description", "")), str(want.get("description", ""))):
            return False
        for key in NUMERIC_KEYS:
            if Decimal(str(got.get(key, "0"))) != Decimal(str(want.get(key, "0"))):
                return False
    return True


CRITICAL_FIELDS = ("customer_name", "issue_date", "currency", "line_items")


def _critical_match(result: ExtractionResult, expected: Mapping[str, Any]) -> bool:
    if not str(result.fields.get("title") or "").strip():
        return False
    for field in CRITICAL_FIELDS:
        if field == "line_items":
            if not _same_items(result.fields.get(field), expected.get(field)):
                return False
        elif result.fields.get(field) != expected.get(field):
            return False
    return True


def load_cases(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        for raw in json.loads(Path(path).read_text(encoding="utf-8")):
            request = dict(raw["request"])
            if "brief_repeat" in raw:
                request["brief"] = str(request["brief"]) * int(raw["brief_repeat"])
            cases.append({**raw, "request": request})
    return cases


def run_shadow(
    adapter: ExtractionAdapter,
    cases: list[dict[str, Any]],
    *,
    ledger_root: str | Path,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ``adapter`` on ``cases`` and return a metadata-only shadow report."""

    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ledger = BudgetLedger(ledger_root)
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = case["request"]
        job_id = f"shadow-{stamp}-{case['id']}"
        row: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", request.get("locale", "")),
            "split": "dev" if case["id"] in dev else "held_out",
            "expected": case["expected"],
        }
        try:
            reservation = ledger.reserve(job_id, calls=1, input_tokens=0, output_tokens=2_000)
        except BudgetExceeded:
            row.update(status="rejected", issue_codes=["budget_exceeded"], model_calls=0)
            rows.append(row)
            continue
        result = adapter.extract(
            str(request["brief"]),
            locale=str(request.get("locale", "th-TH")),
            template_id=str(request.get("template_id", "invoice")),
            template_version=str(request.get("template_version", "1")),
            budget=reservation,
        )
        issues = validate_extraction_result(result)
        status = "rejected" if result.status == "rejected" or issues else result.status
        # Shadow judges "never guess": only critical fields the brief truly leaves out must be missing.
        expected_missing = set(case.get("expected_missing_critical", set(case.get("expected_missing", [])) & set(CRITICAL_FIELDS)))
        row.update(
            status=status,
            issue_codes=sorted({issue["code"] for issue in [*result.issues, *issues]}),
            model_calls=result.model_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            computed_total=any(key in result.fields for key in ("subtotal", "tax_total", "grand_total", "derived_totals")),
            critical_match=status == "complete" and _critical_match(result, case.get("expected_fields", {})),
            abstained_on_expected_missing=status != "complete" and expected_missing <= set(result.missing),
            silent_pass=case["category"] == "adversarial" and status == "complete",
        )
        rows.append(row)
    report = _summarize(rows, stamp)
    serialized = json.dumps(report, ensure_ascii=False)
    leaks = [case["id"] for case in cases if case.get("canary") and (case["canary"] in serialized or ledger_contains(ledger.path, case["canary"]))]
    report["metrics"]["canary_leaks"] = len(leaks)
    report["gate"]["checks"]["canary_leaks_zero"] = not leaks
    report["gate"]["passed"] = all(report["gate"]["checks"].values())
    return report


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _summarize(rows: list[dict[str, Any]], stamp: str) -> dict[str, Any]:
    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    adversarial = [row for row in held if row["category"] == "adversarial"]
    critical_rate = _rate(sum(1 for row in clear if row.get("critical_match")), len(clear))
    recall = _rate(sum(1 for row in ambiguous if row.get("abstained_on_expected_missing")), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "critical_exact_match_clear": critical_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in adversarial if row.get("silent_pass")),
        "model_computed_totals": sum(1 for row in rows if row.get("computed_total")),
        "max_model_calls_per_job": max((int(row.get("model_calls", 0)) for row in rows), default=0),
        "model_calls_total": sum(int(row.get("model_calls", 0)) for row in rows),
        "input_tokens_total": sum(int(row.get("input_tokens", 0)) for row in rows),
        "output_tokens_total": sum(int(row.get("output_tokens", 0)) for row in rows),
        "canary_leaks": 0,
    }
    checks = {
        "critical_exact_match_ge_95pct": critical_rate is not None and critical_rate >= CRITICAL_MATCH_FLOOR,
        "needs_input_recall_100pct": recall is not None and recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "model_computed_totals_zero": metrics["model_computed_totals"] == 0,
        "model_calls_per_job_le_2": metrics["max_model_calls_per_job"] <= 2,
        "canary_leaks_zero": True,
    }
    return {
        "schema_version": SHADOW_REPORT_SCHEMA,
        "run_id": stamp,
        "suite": "EVAL-DOC-brief60",
        "mode": "shadow",
        "results_used": False,
        "metrics": metrics,
        "gate": {"passed": all(checks.values()), "checks": checks},
        "cases": rows,
    }


__all__ = ["CRITICAL_MATCH_FLOOR", "SHADOW_REPORT_SCHEMA", "load_cases", "run_shadow"]
