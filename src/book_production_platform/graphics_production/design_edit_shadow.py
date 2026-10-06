"""Shadow evaluation of the AI design editor on EVAL-GFX-edit24.

Each case gives a base request and either an instruction (model) or a
structured patch (no model). Clear cases list the design checks that must hold
after the edit, compared on the theme's design controls before and after, and
the targets the edit may touch; changing anything else counts as a miss. Rows
carry ids, categories, statuses, and counters only (no instruction text).

EVAL-GFX-edit-art20 adds art style and photo cases with the same gate, plus
``new_look`` cases (a look no built-in pack covers) that must all end with a
newly designed art style when the editor has a style designer.

Gate (held-out cases only): clear edits exact >= 95%, ``needs_input`` recall on
ambiguous instructions 100%, zero adversarial silent passes, structured cases
decided with zero model calls and the expected status, at most two calls per
job, and zero canary leaks.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import finalize_report, rate
from book_production_platform.graphics_ir import ImagePlacement

from .art_styles import ART_STYLES_DIR
from .design_edit import DesignEditor, DesignEditResult, apply_change
from .templates import controls_for

BUILTIN_STYLE_IDS = frozenset(path.stem for path in ART_STYLES_DIR.glob("*.json"))

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


_REGIONS = {
    "top_left": (False, False), "top_right": (True, False), "bottom_left": (False, True), "bottom_right": (True, True),
}


def _placement(controls: Mapping[str, Any], check: Mapping[str, Any]) -> ImagePlacement | None:
    """The photo a check names by ``image`` (placement id) or ``asset`` (first placement of that asset)."""

    for entry in controls.get("images", []):
        if ("image" in check and entry.get("id") == check["image"]) or ("asset" in check and entry.get("asset") == check["asset"]):
            return ImagePlacement.model_validate(entry)
    return None


def _compare(value: Any, check: Mapping[str, Any]) -> bool:
    if "equals" in check:
        return bool(value == check["equals"])
    if "in" in check:
        return value in check["in"]
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return ("gt" not in check or value > check["gt"]) and ("lt" not in check or value < check["lt"])


def _photo_check(check: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    placement = _placement(after, check)
    if check.get("absent"):
        return placement is None
    if placement is None:
        return False
    if "region" in check:
        canvas = after["canvas"]
        height = placement.width * canvas["width"] / canvas["height"] if placement.square else placement.height
        cx, cy = placement.x + placement.width / 2, placement.y + height / 2
        if check["region"] == "full":
            return placement.width >= 0.85 and height >= 0.85
        right, bottom = _REGIONS[check["region"]]
        return (cx > 0.55 if right else cx < 0.45) and (cy > 0.55 if bottom else cy < 0.45)
    if "effect" in check:
        return _compare(getattr(placement.effects, check["effect"]), check)
    return _compare(getattr(placement, check["field"]), check)


def _check(check: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any], content: Mapping[str, str]) -> bool:
    tolerance = float(check.get("tol", 2 if "delta" in check else 0.03))
    if "art_style" in check:
        return bool(after["art_style"] == check["art_style"])
    if "art_style_new" in check:
        return after["art_style"] is not None and after["art_style"] not in BUILTIN_STYLE_IDS and after["art_style"] != before["art_style"]
    if "keep_brand_colors" in check:
        return bool(after["keep_brand_colors"] == check["keep_brand_colors"])
    if "image" in check or "asset" in check:
        return _photo_check(check, after)
    if "image_count" in check:
        return bool(len(after.get("images", [])) == check["image_count"])
    if "style" in check:
        return bool(after["style"] == check["style"])
    if "content" in check:
        return bool(content.get(check["content"]) == check["equals"])
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


def _failed_checks(result: DesignEditResult, expected: Mapping[str, Any], base: Mapping[str, Any]) -> list[int | str]:
    """Indexes of the expected checks that failed (``"targets"`` when the edit touched other parts); no text."""

    if result.status != "complete" or result.request is None:
        return ["status"]
    failed: list[int | str] = []
    allowed = list(expected.get("targets", []))
    if not all(any(fnmatch.fnmatchcase(changed, pattern) for pattern in allowed) for changed in result.changed):
        failed.append("targets")
    before = controls_for(dict(base))
    after = controls_for(result.request)
    content = result.request.get("content", {})
    failed += [index for index, check in enumerate(expected.get("checks", [])) if not _check(check, before, after, content)]
    return failed


def _exact(result: DesignEditResult, expected: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    if result.status != "complete" or result.request is None:
        return False
    allowed = list(expected.get("targets", []))
    # "image:*" allows any photo id (the model names new photos itself).
    if not all(any(fnmatch.fnmatchcase(changed, pattern) for pattern in allowed) for changed in result.changed):
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
    suite: str = SUITE,
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
            # Per job: the editor's calls; the style designer runs as its own job (style_model_calls).
            "model_calls": result.model_calls - result.style_model_calls,
            "style_model_calls": result.style_model_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "status_match": _status_match(result.status, expected.get("status")),
            "exact": (_exact(result, expected, base)
                      if case["category"] in ("clear", "structured", "new_look") and expected.get("status") == "complete" else None),
            "silent_pass": case["category"] == "adversarial" and result.status == "complete",
            "failed_checks": (_failed_checks(result, expected, base)
                              if case["category"] in ("clear", "structured", "new_look") and expected.get("status") == "complete" else []),
        })
    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    new_look = [row for row in held if row["category"] == "new_look"]
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
        "model_calls_total": sum(int(row["model_calls"]) + int(row["style_model_calls"]) for row in rows),
        "max_style_model_calls_per_job": max((int(row["style_model_calls"]) for row in rows), default=0),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "edit_exact_clear_ge_95pct": exact_rate is not None and exact_rate >= EXACT_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "structured_zero_calls_and_expected": structured_ok == 1.0,
    }
    if new_look:
        # Looks no built-in pack covers: the art style designer must create one that passes its checks.
        metrics["new_look_complete"] = rate(sum(1 for row in new_look if row["exact"]), len(new_look))
        checks["new_look_all_created"] = metrics["new_look_complete"] == 1.0
        checks["style_calls_per_job_le_2"] = metrics["max_style_model_calls_per_job"] <= 2
    return finalize_report(
        suite=suite, tool_id=TOOL_ID, run_id=stamp, rows=rows, metrics=metrics, checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases], ledger_path=editor.ledger.path,
    )


__all__ = ["EXACT_FLOOR", "SUITE", "run_edit_shadow"]
