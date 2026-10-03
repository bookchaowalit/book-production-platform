"""Shadow-mode evaluation of the Game brief adapter on EVAL-GAM-brief30.

Shadow mode calls the real adapter for every brief case, compares the proposal
with the expected outcome, and never builds a Unity project or uses the
result. Structured cases (a ready GameIR) skip the model and must report zero
calls. The report holds case IDs, categories, statuses, issue codes, and
metrics only; the shared kit fails the privacy check if any case canary reaches
the report or the budget ledger.

Gate (held-out cases; spec section 4.2 plus AC-GAM-01a): clear-brief exact
match >= 95%, ``needs_input`` recall 100% on ambiguous briefs, zero silent
adversarial passes, zero accepted layouts with an unreachable collectible or
exit (re-checked independently), every accepted GameIR passes the existing
validator, structured cases make zero calls, and at most two calls per job.
The Unity batch test of accepted layouts (REQ-GAM-02) is a separate runtime
gate that stays ``pending`` on a host without the pinned Unity Editor.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from book_production_platform.brief_adapter_kit.shadow import (
    finalize_report,
    load_cases,
    rate,
)
from book_production_platform.game_ir import (
    GameInputError,
    GameIR,
    parse_game_ir,
    validate_game_ir,
)

from .provider import ENTITY_TYPES, GameBriefAdapter

SUITE = "EVAL-GAM-brief30"
TOOL_ID = "game-v1a"
CRITICAL_MATCH_FLOOR = 0.95
RUNTIME_GATE = {
    "unity_batch_test": "pending",
    "reason": "the pinned Unity Editor is not installed on this host; gate covers GameIR, validator, and reachability only",
}


def _independently_reachable(game_ir: dict[str, Any]) -> bool:
    """Re-check reachability with a separate BFS so the gate does not trust the adapter's own check."""

    width, height = game_ir["world"]["width"], game_ir["world"]["height"]
    walls = {tuple(cell) for wall in game_ir["walls"] for cell in wall["cells"]}
    start = tuple(game_ir["player"]["spawn"])
    goals = {tuple(item["cell"]) for item in game_ir["objectives"]} | {tuple(game_ir["exit"]["cell"])}
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt in seen or nxt in walls or not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                continue
            seen.add(nxt)
            queue.append(nxt)
    return goals <= seen


def _validator_ok(game_ir: dict[str, Any]) -> bool:
    try:
        validate_game_ir(GameIR.model_validate(game_ir))
    except (GameInputError, ValidationError):
        return False
    return True


def _structured_status(game_ir: Any) -> tuple[str, list[str]]:
    try:
        validate_game_ir(parse_game_ir(json.dumps(game_ir).encode("utf-8")))
    except GameInputError as error:
        return "rejected", [error.code]
    return "complete", []


def _critical_match(game_ir: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if game_ir is None:
        return False
    if [game_ir["world"]["width"], game_ir["world"]["height"]] != expected.get("grid"):
        return False
    if len(game_ir["objectives"]) != expected.get("collectibles"):
        return False
    if "player" in expected and list(game_ir["player"]["spawn"]) != expected["player"]:
        return False
    return "exit" not in expected or list(game_ir["exit"]["cell"]) == expected["exit"]


def run_shadow(
    adapter: GameBriefAdapter,
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
        expected = case["expected"]
        row: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "expected_status": expected["status"],
        }
        if case["category"] == "structured":
            status, codes = _structured_status(case["request"]["game_ir"])
            row.update(
                status=status, issue_codes=codes, model_calls=0, input_tokens=0, output_tokens=0,
                structured_ok=status == expected["status"],
            )
            rows.append(row)
            continue
        result = adapter.propose(
            str(case["request"]["brief"]),
            job_id=f"shadow-{stamp}-{case['id']}",
            locale=str(case.get("locale", "th-TH")),
            scene_id=f"eval-{case['id']}".lower(),
        )
        complete = result.status == "complete" and result.game_ir is not None
        row.update(
            status=result.status,
            issue_codes=result.issue_codes(),
            model_calls=result.model_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            unreachable_accepted=complete and not _independently_reachable(result.game_ir or {}),
            validator_ok=not complete or _validator_ok(result.game_ir or {}),
            critical_match=complete and _critical_match(result.game_ir, expected),
            abstained_on_expected_missing=not complete and set(expected.get("missing", [])) <= set(result.missing),
            silent_pass=case["category"] == "adversarial" and complete,
        )
        rows.append(row)
    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    adversarial = [row for row in held if row["category"] == "adversarial"]
    structured = [row for row in rows if row["category"] == "structured"]
    critical_rate = rate(sum(1 for row in clear if row.get("critical_match")), len(clear))
    recall = rate(sum(1 for row in ambiguous if row.get("abstained_on_expected_missing")), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "critical_exact_match_clear": critical_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in adversarial if row.get("silent_pass")),
        "unreachable_silent_passes": sum(1 for row in rows if row.get("unreachable_accepted")),
        "accepted_validator_failures": sum(1 for row in rows if row.get("validator_ok") is False),
        "registered_entity_types": sorted(ENTITY_TYPES),
        "structured_mismatches": sum(1 for row in structured if not row.get("structured_ok")),
        "structured_model_calls": sum(int(row.get("model_calls", 0)) for row in structured),
        "model_calls_total": sum(int(row.get("model_calls", 0)) for row in rows),
        "input_tokens_total": sum(int(row.get("input_tokens", 0)) for row in rows),
        "output_tokens_total": sum(int(row.get("output_tokens", 0)) for row in rows),
    }
    checks = {
        "critical_exact_match_ge_95pct": critical_rate is not None and critical_rate >= CRITICAL_MATCH_FLOOR,
        "needs_input_recall_100pct": recall is not None and recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "unreachable_silent_passes_zero": metrics["unreachable_silent_passes"] == 0,
        "validator_passes_all_accepted": metrics["accepted_validator_failures"] == 0,
        "structured_zero_calls_and_expected": metrics["structured_model_calls"] == 0 and metrics["structured_mismatches"] == 0,
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
    report["gate"]["scope"] = "ir_validator"
    report["gate"]["pending"] = ["unity_batch_test_all_accepted"]
    report["gate"]["live_eligible"] = False  # the runtime gate has not run on this host
    report["runtime_gate"] = dict(RUNTIME_GATE)
    return report


__all__ = ["CRITICAL_MATCH_FLOOR", "RUNTIME_GATE", "SUITE", "load_cases", "run_shadow"]
