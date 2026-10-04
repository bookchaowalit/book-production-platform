"""Shadow evaluation of the AI design editor on EVAL-GFX-edit24.

Each case gives a base request and either an instruction (model) or a
structured patch (no model). Clear cases list the design checks that must hold
after the edit, compared on the theme's design controls before and after, and
the targets the edit may touch; changing anything else counts as a miss. Rows
carry ids, categories, statuses, and counters only (no instruction text).

Gate (held-out cases only): clear edits exact >= 95%, ``needs_input`` recall on
ambiguous instructions 100%, zero adversarial silent passes, structured cases
decided with zero model calls and the expected status, at most two calls per
job, and zero canary leaks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import finalize_report, rate

from .design_edit import DesignEditor, DesignEditResult, apply_change
from .templates import controls_for

SUITE = "EVAL-GFX-edit24"
TOOL_ID = "graphics-v1a"
EXACT_FLOOR = 0.95


def _param(controls: Mapping[str, Any], target: str, name: str) -> Any:
    for layer in controls["layers"]:
        for item in layer["items"]:
            if item["target"] == target:
                return item["params"].get(name, item.get(name))
    return None


def _layer(controls: Mapping[str, Any], layer_id: str) -> Mapping[str, Any] | None:
    return next((layer for layer in controls["layers"] if layer["id"] == layer_id), None)


def _check(check: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any], content: Mapping[str, str]) -> bool:
    tolerance = float(check.get("tol", 2 if "delta" in check else 0.03))
    if "style" in check:
        return after["style"] == check["style"]
    if "content" in check:
        return content.get(check["content"]) == check["equals"]
    if "hidden" in check:
        layer = _layer(after, check["hidden"])
        return layer is not None and (not layer["visible"] or all(not item["visible"] for item in layer["items"]))
    if "layer" in check:
        layer = _layer(after, check["layer"])
        if layer is None:
            return False
        value = layer[check["field"]]
        if isinstance(check["equals"], bool) or not isinstance(value, int | float):
            return bool(value == check["equals"])
        return abs(float(value) - float(check["equals"])) <= tolerance
    if "item" in check:
        new, old = _param(after, check["item"], check["param"]), _param(before, check["item"], check["param"])
        if "equals" in check:
            return bool(new == check["equals"])
        if not isinstance(new, int | float) or not isinstance(old, int | float):
            return False
        if "delta" in check:
            return abs((new - old) - float(check["delta"])) <= tolerance
        if "ratio" in check:
            return old != 0 and abs(new / old - float(check["ratio"])) <= tolerance
    return False


def _exact(result: DesignEditResult, expected: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    if result.status != "complete" or result.request is None:
        return False
    allowed = set(expected.get("targets", []))
    if not set(result.changed) <= allowed:
        return False
    before = controls_for(dict(base))
    after = controls_for(result.request)
    content = result.request.get("content", {})
    return all(_check(check, before, after, content) for check in expected.get("checks", []))


def _status_match(status: str, expected: str | None) -> bool:
    if expected == "rejected_or_needs_input":
        return status in ("rejected", "needs_input")
    return status == expected


def run_edit_shadow(
    editor: DesignEditor,
    cases: list[dict[str, Any]],
    *,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for case in cases:
        request, expected = case["request"], case.get("expected", {})
        base = request["base"]
        if "patch" in request:
            result = apply_change(base, request["patch"])
        else:
            result = editor.edit(base, str(request["instruction"]), job_id=f"edit-shadow-{stamp}-{case['id']}",
                                 locale=str(case.get("locale", "th-TH")))
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev or case.get("split") == "dev" else "held_out",
            "input": "structured" if "patch" in request else "instruction",
            "expected_status": expected.get("status"),
            "status": result.status,
            "changed": len(result.changed),
            "issue_codes": result.issue_codes,
            "model_calls": result.model_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "status_match": _status_match(result.status, expected.get("status")),
            "exact": _exact(result, expected, base) if case["category"] in ("clear", "structured") and expected.get("status") == "complete" else None,
            "silent_pass": case["category"] == "adversarial" and result.status == "complete",
        })
    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    structured = [row for row in held if row["category"] == "structured"]
    exact_rate = rate(sum(1 for row in clear if row["exact"]), len(clear))
    recall = rate(sum(1 for row in ambiguous if row["status"] == "needs_input"), len(ambiguous))
    structured_ok = rate(
        sum(1 for row in structured if row["status_match"] and row["model_calls"] == 0 and row["exact"] is not False), len(structured)
    )
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "edit_exact_clear": exact_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in held if row["silent_pass"]),
        "structured_pass_rate": structured_ok,
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "edit_exact_clear_ge_95pct": exact_rate is not None and exact_rate >= EXACT_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "structured_zero_calls_and_expected": structured_ok == 1.0,
    }
    return finalize_report(
        suite=SUITE, tool_id=TOOL_ID, run_id=stamp, rows=rows, metrics=metrics, checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases], ledger_path=editor.ledger.path,
    )


__all__ = ["EXACT_FLOOR", "SUITE", "run_edit_shadow"]
