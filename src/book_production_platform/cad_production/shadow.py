"""Shadow-mode evaluation of the CAD brief adapter on EVAL-CAD-brief30.

Shadow mode calls the real adapter for every brief case, compares the proposal
with the expected outcome, and never builds a part or uses the result.
Structured cases (a ready PartIR) skip the model and must report zero calls.
The report holds case IDs, categories, statuses, issue codes, and metrics only;
it never holds brief text, model output, or dimension values, and the shared
kit fails the privacy check if any case canary reaches the report or ledger.

Gate (held-out cases; spec section 4.2 plus AC-CAD-01a): clear-brief exact
match >= 95%, ``needs_input`` recall 100% on ambiguous briefs, zero silent
adversarial passes, zero guessed dimensions on any case, every accepted PartIR
passes the geometry invariants, structured cases make zero calls, and at most
two calls per job. The FreeCAD build of accepted proposals is a separate
runtime gate that stays ``pending`` on a host without the pinned FreeCADCmd.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import (
    finalize_report,
    load_cases,
    rate,
)
from book_production_platform.cad_ir import PartIRError, parse_part_ir

from .provider import CadBriefAdapter

SUITE = "EVAL-CAD-brief30"
TOOL_ID = "cad-v1a"
CRITICAL_MATCH_FLOOR = 0.95
RUNTIME_GATE = {
    "freecad_build": "pending",
    "reason": "the pinned FreeCADCmd runtime is not installed on this host; gate covers PartIR and validator only",
}


def _structured_row(case: dict[str, Any]) -> dict[str, Any]:
    try:
        parse_part_ir(case["request"]["part_ir"])
    except PartIRError as error:
        return {"status": "rejected", "issue_codes": [error.code], "model_calls": 0, "parameters": {}}
    return {"status": "complete", "issue_codes": [], "model_calls": 0, "parameters": {}}


def _guessed(parameters: dict[str, str], expected_mm: dict[str, str]) -> int:
    """Dimensions kept by the adapter that the brief does not state (per the fixture's labels)."""

    return sum(
        1
        for name, value in parameters.items()
        if name not in expected_mm or Decimal(value) != Decimal(expected_mm[name])
    )


def _geometry_ok(part_ir: dict[str, Any] | None) -> bool:
    if part_ir is None:
        return False
    try:
        parse_part_ir(part_ir)
    except PartIRError:
        return False
    return True


def run_shadow(
    adapter: CadBriefAdapter,
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
        expected_mm: dict[str, str] = dict(expected.get("stated_mm", {}))
        row: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "expected_status": expected["status"],
        }
        if case["category"] == "structured":
            outcome = _structured_row(case)
            row.update(
                status=outcome["status"],
                issue_codes=outcome["issue_codes"],
                model_calls=0,
                input_tokens=0,
                output_tokens=0,
                guessed_dimensions=0,
                geometry_ok=outcome["status"] != "complete" or _geometry_ok(case["request"]["part_ir"]),
                structured_ok=outcome["status"] == expected["status"],
            )
            rows.append(row)
            continue
        result = adapter.propose(
            str(case["request"]["brief"]),
            job_id=f"shadow-{stamp}-{case['id']}",
            locale=str(case.get("locale", "th-TH")),
            part_id=f"eval-{case['id']}",
        )
        complete = result.status == "complete"
        row.update(
            status=result.status,
            issue_codes=result.issue_codes(),
            model_calls=result.model_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            guessed_dimensions=_guessed(result.parameters, expected_mm),
            geometry_ok=not complete or _geometry_ok(result.part_ir),
            critical_match=complete and all(
                Decimal(result.parameters.get(name, "-1")) == Decimal(value) for name, value in expected_mm.items()
            ) and len(result.parameters) == len(expected_mm),
            abstained_on_expected_missing=not complete and set(expected.get("missing", [])) <= set(result.missing),
            silent_pass=case["category"] == "adversarial" and complete,
        )
        rows.append(row)
    held = [row for row in rows if row["split"] == "held_out"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    adversarial = [row for row in held if row["category"] == "adversarial"]
    structured = [row for row in rows if row["category"] == "structured"]
    accepted = [row for row in rows if row["status"] == "complete"]
    critical_rate = rate(sum(1 for row in clear if row.get("critical_match")), len(clear))
    recall = rate(sum(1 for row in ambiguous if row.get("abstained_on_expected_missing")), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "critical_exact_match_clear": critical_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in adversarial if row.get("silent_pass")),
        "guessed_dimensions": sum(int(row.get("guessed_dimensions", 0)) for row in rows),
        "accepted_geometry_invariant_failures": sum(1 for row in accepted if not row.get("geometry_ok")),
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
        "guessed_dimensions_zero": metrics["guessed_dimensions"] == 0,
        "geometry_invariants_pass_all_accepted": metrics["accepted_geometry_invariant_failures"] == 0,
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
    report["gate"]["pending"] = ["freecad_build_all_accepted"]
    report["gate"]["live_eligible"] = False  # the runtime gate has not run on this host
    report["runtime_gate"] = dict(RUNTIME_GATE)
    return report


__all__ = ["CRITICAL_MATCH_FLOOR", "RUNTIME_GATE", "SUITE", "load_cases", "run_shadow"]
