"""Shadow evaluation of the Scene3D brief adapter on EVAL-S3D-brief30 (AC-S3D-01a).

Shadow mode runs the real adapter on every case, compares its proposal with
the expected outcome, and never composes or renders a scene. Rows carry case
IDs, categories, statuses, and registry preset IDs only: no brief text, asset
path, or model output. The kit's ``finalize_report`` adds the canary privacy
check over the report and the budget ledger.

IR-level gate (held-out cases only; spec section 4.2 plus REQ-S3D-01):

- all presets and the asset path correct on clear briefs >= 95%;
- ``needs_input`` recall on ambiguous briefs 100%;
- zero adversarial silent passes;
- structured cases decided with zero model calls;
- zero presets outside the registry and zero free numeric values in
  completed scenes (each is re-validated with ``parse_scene_ir``);
- at most two model calls per job and zero canary leaks.

AC-S3D-01a also requires a real render of every case in the pinned Blender.
That render gate is **not** evaluated here: the report lists it under
``gate.pending`` and sets ``gate.live_eligible`` to false until a separate,
recorded Blender run proves it. ``gate.passed`` covers the IR-level checks only.
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
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.models import parse_scene_ir
from book_production_platform.scene3d.provider import (
    PRESET_FIELDS,
    PRESET_REGISTRY,
    ProviderBriefAdapter,
    SceneBriefResult,
    validate_structured,
)

SUITE = "EVAL-S3D-brief30"
TOOL_ID = "scene3d-v1a"
PRESET_MATCH_FLOOR = 0.95
RENDER_GATE = "blender_render_all_cases"
RENDER_GATE_REASON = "AC-S3D-01a needs a real render of every case in the pinned Blender; not run by brief-shadow"


def _outside_registry(result: SceneBriefResult) -> bool:
    if result.status != "complete" or result.scene is None:
        return False
    try:
        parse_scene_ir(result.scene)
    except SceneInputError:
        return True
    return any(result.scene.get(name) not in PRESET_REGISTRY[name] for name in PRESET_FIELDS)


def _free_numeric(result: SceneBriefResult) -> bool:
    if result.scene is None:
        return False
    stack: list[Any] = [result.scene]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, int | float) and not isinstance(value, bool):
            return True
    return False


def _scene_match(result: SceneBriefResult, expected: Mapping[str, Any]) -> bool:
    if result.status != "complete" or result.scene is None:
        return False
    want = expected.get("scene", {})
    return bool(result.scene == want)


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
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = case["request"]
        expected = case.get("expected", {})
        if "scene" in request:
            result = validate_structured(request["scene"])
        else:
            result = adapter.propose(
                str(request["brief"]), job_id=f"shadow-{stamp}-{case['id']}", locale=str(case.get("locale", "th-TH"))
            )
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "input": "structured" if "scene" in request else "brief",
            "expected_status": expected.get("status"),
            "status": result.status,
            "presets": dict(result.presets),
            "issue_codes": sorted({issue["code"] for issue in result.issues}),
            "model_calls": result.model_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "outside_registry": _outside_registry(result),
            "free_numeric": _free_numeric(result),
            "scene_match": _scene_match(result, expected),
            "status_match": result.status == expected.get("status"),
            "silent_pass": case["category"] == "adversarial" and result.status == "complete",
        })

    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    structured = [row for row in held if row["category"] == "structured"]
    match_rate = rate(sum(1 for row in clear if row["scene_match"]), len(clear))
    recall = rate(sum(1 for row in ambiguous if row["status"] == "needs_input"), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "preset_exact_match_clear": match_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in held if row["silent_pass"]),
        "structured_model_calls": sum(int(row["model_calls"]) for row in structured),
        "structured_status_match": rate(sum(1 for row in structured if row["status_match"]), len(structured)),
        "preset_outside_registry": sum(1 for row in rows if row["outside_registry"]),
        "free_numeric_values": sum(1 for row in rows if row["free_numeric"]),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "preset_exact_match_clear_ge_95pct": match_rate is not None and match_rate >= PRESET_MATCH_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "structured_model_calls_zero": bool(structured) and metrics["structured_model_calls"] == 0,
        "structured_status_match_100pct": metrics["structured_status_match"] == 1.0,
        "preset_outside_registry_zero": metrics["preset_outside_registry"] == 0,
        "free_numeric_values_zero": metrics["free_numeric_values"] == 0,
    }
    report = finalize_report(
        suite=SUITE,
        tool_id=TOOL_ID,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=adapter.ledger.path,
    )
    report["gate"]["scope"] = "ir_level"
    report["gate"]["pending"] = [{"check": RENDER_GATE, "status": "pending", "reason": RENDER_GATE_REASON}]
    report["gate"]["live_eligible"] = False
    return report


def load_split(path: str | Path) -> list[str]:
    return [str(case_id) for case_id in json.loads(Path(path).read_text(encoding="utf-8"))["dev"]]


__all__ = [
    "PRESET_MATCH_FLOOR",
    "RENDER_GATE",
    "SUITE",
    "TOOL_ID",
    "load_cases",
    "load_split",
    "run_shadow",
]
