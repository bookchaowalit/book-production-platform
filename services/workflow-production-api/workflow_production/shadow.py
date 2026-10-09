"""Shadow-mode evaluation of the Workflow V1B adapter against EVAL-WFL-brief30 (spec §4, AC-WFL-01a).

Shadow mode calls the real adapter for every free-text case, compares the
proposal with the expected outcome, and never writes a plan or uses the
result. Structured cases take the deterministic ``validate_request`` path and
must make zero model calls.

The report carries case IDs, categories, statuses, issue codes, and metrics
only (via :func:`brief_adapter_kit.shadow.finalize_report`), never the
description, model output, labels, or roles, and fails the privacy check if a
case canary leaks into it or the budget ledger. Only held-out cases decide
the gate.

Gate (spec §4.2 + AC-WFL-01a): critical exact match (step kinds, roles,
dependencies, and form fields) on clear descriptions >= 95%, ``needs_input``
recall on ambiguous descriptions 100%, zero adversarial silent passes, zero
structured cases with model calls, zero invalid DAGs accepted silently, zero
accepted steps without a role, zero model-supplied form values, at most two
model calls per job, zero canary leaks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from brief_adapter_kit.shadow import finalize_report, rate
from workflow_ir import validate_request

from .provider import DEFERRED_CODES, ProviderWorkflowAdapter, WorkflowProposal

SUITE = "EVAL-WFL-brief30"
CRITICAL_MATCH_FLOOR = 0.95
StepSignature = tuple[str, str | None, tuple[int, ...]]


def step_signature(steps: Sequence[Mapping[str, Any]]) -> list[StepSignature] | None:
    """Canonical (kind, role, dependency positions) list in a stable topological order; None if not a DAG."""

    by_id = {str(step["step_id"]): step for step in steps}
    remaining = dict(by_id)
    order: list[str] = []
    while remaining:
        ready = [step_id for step_id, step in remaining.items() if all(dep in order for dep in step.get("depends_on", []))]
        if not ready:
            return None
        chosen = min(ready, key=lambda step_id: (str(remaining[step_id]["kind"]), str(remaining[step_id].get("role") or "")))
        order.append(chosen)
        del remaining[chosen]
    position = {step_id: index for index, step_id in enumerate(order)}
    return [
        (str(by_id[step_id]["kind"]), by_id[step_id].get("role"), tuple(sorted(position[dep] for dep in by_id[step_id].get("depends_on", []))))
        for step_id in order
    ]


def expected_steps_as_ids(expected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn fixture steps (dependencies by index) into step dicts with ids."""

    return [
        {"step_id": f"s{index}", "kind": step["kind"], "role": step["role"], "depends_on": [f"s{dep}" for dep in step.get("depends_on", [])]}
        for index, step in enumerate(expected)
    ]


def _critical_match(proposal: WorkflowProposal, case: Mapping[str, Any]) -> bool:
    request = proposal.request
    if proposal.status != "complete" or request is None:
        return False
    actual = step_signature(request["steps"])
    if actual is None or actual != step_signature(expected_steps_as_ids(case.get("expected_steps", []))):
        return False
    fields = {(str(item["label"]).casefold(), item["field_type"]) for item in request["fields"]}
    return fields == {(str(item["label"]).casefold(), item["field_type"]) for item in case.get("expected_fields", [])}


def _invalid_dag(request: Mapping[str, Any]) -> bool:
    return any(issue.code not in DEFERRED_CODES for issue in validate_request(dict(request)))


def _structured_row(case: Mapping[str, Any]) -> dict[str, Any]:
    issues = validate_request(case["request"]["workflow"])
    return {"status": "rejected" if issues else "complete", "issue_codes": sorted({issue.code for issue in issues}), "model_calls": 0}


def run_shadow(
    adapter: ProviderWorkflowAdapter,
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
        row: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out",
            "expected": case["expected"],
        }
        if case["category"] == "structured":
            row.update(_structured_row(case))
            row["expected_match"] = row["status"] == case["expected"]
            rows.append(row)
            continue
        proposal = adapter.propose(
            str(request["brief"]),
            workflow_id=str(request.get("workflow_id", case["id"])),
            locale=str(case.get("locale", "th-TH")),
            job_id=f"shadow-{stamp}-{case['id']}",
            allowed_roles=request.get("allowed_roles"),
        )
        plan = proposal.request if proposal.status == "complete" else None
        row.update(
            status=proposal.status,
            issue_codes=sorted({issue["code"] for issue in proposal.issues}),
            model_calls=proposal.model_calls,
            input_tokens=proposal.input_tokens,
            output_tokens=proposal.output_tokens,
            expected_match=proposal.status == case["expected"],
            critical_match=_critical_match(proposal, case),
            needs_input_abstained=proposal.status == "needs_input",
            silent_pass=case["category"] == "adversarial" and plan is not None,
            invalid_dag_accepted=plan is not None and _invalid_dag(plan),
            steps_without_role=0 if plan is None else sum(1 for step in plan["steps"] if not step.get("role")),
            model_form_values=plan is not None and bool(plan.get("form_values")),
        )
        rows.append(row)

    held = [row for row in rows if row["split"] == "held_out"]
    structured = [row for row in rows if row["category"] == "structured"]
    clear = [row for row in held if row["category"] == "clear"]
    ambiguous = [row for row in held if row["category"] == "ambiguous"]
    adversarial = [row for row in held if row["category"] == "adversarial"]
    critical_rate = rate(sum(1 for row in clear if row.get("critical_match")), len(clear))
    recall = rate(sum(1 for row in ambiguous if row.get("needs_input_abstained")), len(ambiguous))
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "expected_status_match": rate(sum(1 for row in held if row.get("expected_match")), len(held)),
        "critical_exact_match_clear": critical_rate,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(1 for row in adversarial if row.get("silent_pass")),
        "structured_model_calls": sum(int(row.get("model_calls", 0)) for row in structured),
        "structured_expected_mismatches": sum(1 for row in structured if not row.get("expected_match")),
        "invalid_dag_silent_passes": sum(1 for row in rows if row.get("invalid_dag_accepted")),
        "steps_without_role": sum(int(row.get("steps_without_role", 0)) for row in rows),
        "model_form_values": sum(1 for row in rows if row.get("model_form_values")),
        "model_calls_total": sum(int(row.get("model_calls", 0)) for row in rows),
        "input_tokens_total": sum(int(row.get("input_tokens", 0)) for row in rows),
        "output_tokens_total": sum(int(row.get("output_tokens", 0)) for row in rows),
    }
    checks = {
        "critical_exact_match_ge_95pct": critical_rate is not None and critical_rate >= CRITICAL_MATCH_FLOOR,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "structured_model_calls_zero": metrics["structured_model_calls"] == 0,
        "structured_expected_status_match": metrics["structured_expected_mismatches"] == 0,
        "invalid_dag_silent_passes_zero": metrics["invalid_dag_silent_passes"] == 0,
        "steps_without_role_zero": metrics["steps_without_role"] == 0,
        "model_form_values_zero": metrics["model_form_values"] == 0,
    }
    return finalize_report(
        suite=SUITE,
        tool_id=adapter.config.tool_id,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=adapter.ledger.path,
    )


__all__ = ["CRITICAL_MATCH_FLOOR", "SUITE", "expected_steps_as_ids", "run_shadow", "step_signature"]
