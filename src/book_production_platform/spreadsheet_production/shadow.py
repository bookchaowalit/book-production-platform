"""Shadow-mode evaluation of the Spreadsheet V1B brief adapter (EVAL-XLS-brief40).

Shadow mode runs the real adapter on every eval case, compares the proposal
with the expected outcome, builds and reopens complete proposals in a
temporary directory, and never uses the result. The report holds case IDs,
categories, statuses, and metrics only — never briefs, headers, values, or
model output — and fails its privacy check if a case canary leaks into the
report or the budget ledger.

Gate (held-out cases only): spec section 4.2 plus AC-XLS-01a — mapping exact
match on clear briefs >= 95%, ``needs_input`` recall on ambiguous briefs
100%, zero silent adversarial passes, zero raw formulas from the model, zero
formula mismatches after reopen, structured cases pass with zero model calls,
at most two calls per job, and zero canary leaks.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import (
    finalize_report,
    load_cases,
    rate,
)

from .formulas import FormulaError
from .provider import SpreadsheetBriefAdapter, SpreadsheetProposalResult
from .qa import qa_workbook
from .recipe import WorkbookBuildError, build_workbook_ir
from .render import RenderError, render_workbook

SUITE = "EVAL-XLS-brief40"
TOOL_ID = "spreadsheet-v1a"
MAPPING_MATCH_FLOOR = 0.95


def _canonical(proposal: Mapping[str, Any] | None) -> str:
    if proposal is None:
        return ""
    intents = sorted(json.dumps(dict(intent), sort_keys=True, ensure_ascii=False) for intent in proposal.get("intents", []))
    return json.dumps(
        {"template_id": proposal.get("template_id"), "mapping": dict(proposal.get("mapping", {})), "intents": intents},
        sort_keys=True,
        ensure_ascii=False,
    )


def _reopen_mismatch(result: SpreadsheetProposalResult, scratch: Path, case_id: str) -> bool:
    if result.status != "complete" or result.request is None:
        return False
    try:
        workbook = build_workbook_ir(result.request)
        path = scratch / f"{case_id}.xlsx"
        render_workbook(workbook, path)
        return bool(qa_workbook(workbook, path))
    except (WorkbookBuildError, FormulaError, RenderError, ValueError):
        return True


def resolve_request(case: Mapping[str, Any], datasets: Mapping[str, Any]) -> dict[str, Any]:
    request = dict(case["request"])
    name = request.get("dataset")
    if isinstance(name, str):
        request["dataset"] = {"name": name, **datasets[name]}
    request.setdefault("workbook_id", f"eval-{case['id']}")
    request.setdefault("locale", case.get("locale", "en-US"))
    return request


def run_shadow(
    adapter: SpreadsheetBriefAdapter,
    cases: list[dict[str, Any]],
    datasets: Mapping[str, Any],
    *,
    ledger_root: str | Path,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ``adapter`` on ``cases`` and return a metadata-only shadow report."""

    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="xls-shadow-") as scratch_dir:
        scratch = Path(scratch_dir)
        for case in cases:
            result = adapter.propose(resolve_request(case, datasets), job_id=f"shadow-{stamp}-{case['id']}")
            expected = str(case["expected"])
            complete = result.status == "complete"
            rows.append({
                "id": case["id"],
                "category": case["category"],
                "locale": case.get("locale", ""),
                "split": "dev" if case["id"] in dev else "held_out",
                "expected": expected,
                **result.metadata(),
                "status_as_expected": result.status == expected if expected != "not_complete" else not complete,
                "mapping_match": complete and _canonical(result.proposal) == _canonical(case.get("expected_proposal")),
                "raw_formula_from_model": result.raw_formula_from_model,
                "reopen_mismatch": _reopen_mismatch(result, scratch, str(case["id"])),
                "silent_pass": case["category"] == "adversarial" and complete,
            })
    held = [row for row in rows if row["split"] == "held_out"]
    by_category = {category: [row for row in held if row["category"] == category] for category in ("structured", "clear", "ambiguous", "adversarial")}
    mapping_rate = rate(sum(1 for row in by_category["clear"] if row["mapping_match"]), len(by_category["clear"]))
    recall = rate(sum(1 for row in by_category["ambiguous"] if row["status"] == "needs_input"), len(by_category["ambiguous"]))
    structured_rate = rate(
        sum(1 for row in by_category["structured"] if row["status_as_expected"] and row["model_calls"] == 0), len(by_category["structured"])
    )
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "status_counts": {status: sum(1 for row in held if row["status"] == status) for status in ("complete", "needs_input", "rejected")},
        "mapping_exact_match_clear": mapping_rate,
        "needs_input_recall_ambiguous": recall,
        "structured_pass_rate": structured_rate,
        "adversarial_silent_passes": sum(1 for row in by_category["adversarial"] if row["silent_pass"]),
        "raw_formula_from_model": sum(1 for row in rows if row["raw_formula_from_model"]),
        "reopen_mismatches": sum(1 for row in rows if row["reopen_mismatch"]),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "mapping_exact_match_ge_95pct": mapping_rate is not None and mapping_rate >= MAPPING_MATCH_FLOOR,
        "needs_input_recall_100pct": recall is not None and recall == 1.0,
        "structured_zero_calls_100pct": structured_rate is not None and structured_rate == 1.0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
        "raw_formula_from_model_zero": metrics["raw_formula_from_model"] == 0,
        "reopen_mismatches_zero": metrics["reopen_mismatches"] == 0,
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


def load_datasets(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    datasets: dict[str, Any] = data if isinstance(data, dict) else {}
    if not datasets:
        raise ValueError("datasets_invalid")
    return datasets


__all__ = ["MAPPING_MATCH_FLOOR", "SUITE", "load_cases", "load_datasets", "resolve_request", "run_shadow"]
