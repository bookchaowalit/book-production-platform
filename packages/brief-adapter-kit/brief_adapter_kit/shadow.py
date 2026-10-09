"""Shared shadow-evaluation helpers (spec section 4).

A tool's shadow run calls its real adapter on every eval case, compares the
proposal with the expected outcome, and never uses the result. Reports carry
case IDs, categories, statuses, and metrics only. Gate decisions use held-out
cases; prompts may only be tuned on dev cases.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SHADOW_REPORT_SCHEMA = "brief-adapter-shadow-report.v1"
CATEGORIES = ("structured", "clear", "ambiguous", "adversarial")


def load_cases(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        cases.extend(json.loads(Path(path).read_text(encoding="utf-8")))
    return cases


def composition(cases: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Count cases per category and language prefix (``th``/``en``) for composition tests."""

    counts: dict[str, Counter[str]] = {category: Counter() for category in CATEGORIES}
    for case in cases:
        counts.setdefault(str(case["category"]), Counter())[str(case.get("locale", ""))[:2]] += 1
    return {category: dict(counter) for category, counter in counts.items()}


def ledger_contains(ledger_path: str | Path, needle: str) -> bool:
    """True when ``needle`` appears in any row of the budget ledger (canary scan)."""

    path = Path(ledger_path)
    if not path.exists():
        return False
    connection = sqlite3.connect(path)
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                if any(needle in str(value) for value in row):
                    return True
        return False
    finally:
        connection.close()


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def finalize_report(
    *,
    suite: str,
    tool_id: str,
    run_id: str,
    rows: list[dict[str, Any]],
    metrics: dict[str, Any],
    checks: dict[str, bool],
    canaries: Iterable[str],
    ledger_path: str | Path,
) -> dict[str, Any]:
    """Assemble a metadata-only report and add the canary-leak privacy check."""

    report: dict[str, Any] = {
        "schema_version": SHADOW_REPORT_SCHEMA,
        "suite": suite,
        "tool_id": tool_id,
        "run_id": run_id,
        "mode": "shadow",
        "results_used": False,
        "metrics": {**metrics, "max_model_calls_per_job": max((int(row.get("model_calls", 0)) for row in rows), default=0)},
        "gate": {"passed": False, "checks": dict(checks)},
        "cases": rows,
    }
    report["gate"]["checks"]["model_calls_per_job_le_2"] = report["metrics"]["max_model_calls_per_job"] <= 2
    serialized = json.dumps(report, ensure_ascii=False)
    leaks = sum(1 for canary in canaries if canary and (canary in serialized or ledger_contains(ledger_path, canary)))
    report["metrics"]["canary_leaks"] = leaks
    report["gate"]["checks"]["canary_leaks_zero"] = leaks == 0
    report["gate"]["passed"] = all(report["gate"]["checks"].values())
    return report


__all__ = ["CATEGORIES", "SHADOW_REPORT_SCHEMA", "composition", "finalize_report", "ledger_contains", "load_cases", "rate"]
