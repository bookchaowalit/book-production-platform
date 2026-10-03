"""Shadow evaluation of the Graphics brief adapter on EVAL-GFX-brief40 (AC-GFX-01a).

Shadow mode runs the real adapter on every case, compares its proposal with
the expected outcome, and never renders or uses the result. Each row carries
the case ID, category, status, registry IDs, and counters only: no brief text,
slot text, or model output. The kit's ``finalize_report`` adds the canary
privacy check over the report and the budget ledger.

Gate (held-out cases only; spec section 4.2 plus AC-GFX-01a):

- template (and variant) correct on clear briefs >= 95%;
- ``needs_input`` recall on ambiguous briefs 100%;
- zero adversarial silent passes;
- structured cases decided with zero model calls;
- zero uncaught overflow: every completed item is re-compiled by the engine;
- zero templates, variants, or brand colors outside the registries;
- at most two model calls per job and zero canary leaks.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import (
    finalize_report,
    load_cases,
    rate,
)

from .provider import (
    TEMPLATE_SLOTS,
    VARIANT_IDS,
    GraphicsBriefResult,
    ProviderBriefAdapter,
    overflows,
    validate_structured,
)

SUITE = "EVAL-GFX-brief40"
TOOL_ID = "graphics-v1a"
TEMPLATE_MATCH_FLOOR = 0.95


def _uncaught_overflow(result: GraphicsBriefResult) -> bool:
    if result.status != "complete" or not result.batch:
        return False
    return any(overflows(item) for item in result.batch.get("items", []))


def _outside_registry(result: GraphicsBriefResult, brand_colors: set[tuple[str, str, str, str]]) -> bool:
    if result.status != "complete" or not result.batch:
        return False
    for item in result.batch.get("items", []):
        brand = item.get("brand", {})
        colors = (str(brand.get("primary")), str(brand.get("accent")), str(brand.get("foreground")), str(brand.get("background")))
        if item.get("template_id") not in TEMPLATE_SLOTS or item.get("variant") not in VARIANT_IDS or colors not in brand_colors:
            return True
    return False


def _template_match(result: GraphicsBriefResult, expected: Mapping[str, Any]) -> bool:
    return (
        result.status == "complete"
        and result.templates == list(expected.get("templates", []))
        and result.variants == list(expected.get("variants", []))
    )


def _content_match(result: GraphicsBriefResult, expected: Mapping[str, Any]) -> bool:
    if result.status != "complete" or not result.batch:
        return False
    return [item.get("content") for item in result.batch.get("items", [])] == list(expected.get("content", []))


def run_shadow(
    adapter: ProviderBriefAdapter,
    cases: list[dict[str, Any]],
    *,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ``adapter`` on ``cases`` and return a metadata-only shadow report."""

    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    brand_colors = {
        (kit.primary, kit.accent, kit.foreground, kit.background) for kit in adapter.brand_kits.values()
    }
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = case["request"]
        expected = case.get("expected", {})
        if "items" in request:
            result = validate_structured(request["items"])
        else:
            result = adapter.propose(
                str(request["brief"]), job_id=f"shadow-{stamp}-{case['id']}", locale=str(case.get("locale", "th-TH"))
            )
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "input": "structured" if "items" in request else "brief",
            "expected_status": expected.get("status"),
            "status": result.status,
            "templates": list(result.templates),
            "issue_codes": sorted({issue["code"] for issue in result.issues}),
            "model_calls": result.model_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "shorten_requested": result.shorten_requested,
            "overflow_caught": result.overflow_caught,
            "uncaught_overflow": _uncaught_overflow(result),
            "outside_registry": _outside_registry(result, brand_colors),
            "template_match": _template_match(result, expected),
            "content_match": _content_match(result, expected),
            "status_match": result.status == expected.get("status"),
            "silent_pass": case["category"] == "adversarial" and result.status == "complete",
        })

    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    structured = [row for row in held if row["category"] == "structured"]
    template_rate = rate(sum(1 for row in clear if row["template_match"]), len(clear))
    recall = rate(sum(1 for row in ambiguous if row["status"] == "needs_input"), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "template_correct_clear": template_rate,
        "content_exact_clear": rate(sum(1 for row in clear if row["content_match"]), len(clear)),
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in held if row["silent_pass"]),
        "structured_model_calls": sum(int(row["model_calls"]) for row in structured),
        "structured_status_match": rate(sum(1 for row in structured if row["status_match"]), len(structured)),
        "uncaught_overflow": sum(1 for row in rows if row["uncaught_overflow"]),
        "overflow_caught": sum(1 for row in rows if row["overflow_caught"]),
        "shorten_requests": sum(1 for row in rows if row["shorten_requested"]),
        "outside_registry": sum(1 for row in rows if row["outside_registry"]),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "template_correct_clear_ge_95pct": template_rate is not None and template_rate >= TEMPLATE_MATCH_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "structured_model_calls_zero": bool(structured) and metrics["structured_model_calls"] == 0,
        "structured_status_match_100pct": metrics["structured_status_match"] == 1.0,
        "uncaught_overflow_zero": metrics["uncaught_overflow"] == 0,
        "outside_registry_zero": metrics["outside_registry"] == 0,
    }
    return finalize_report(
        suite=SUITE,
        tool_id=TOOL_ID,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=adapter.ledger.path,
    )


def load_split(path: str | Path) -> list[str]:
    return [str(case_id) for case_id in json.loads(Path(path).read_text(encoding="utf-8"))["dev"]]


__all__ = [
    "SUITE",
    "TEMPLATE_MATCH_FLOOR",
    "TOOL_ID",
    "load_cases",
    "load_split",
    "run_shadow",
]
