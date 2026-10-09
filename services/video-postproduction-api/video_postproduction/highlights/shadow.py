"""Shadow evaluation of highlight proposals against EVAL-VID-highlight (AC-VID-04a).

Shadow mode runs the real proposer on every case, compares the outcome with
the expectation, and never builds an EDL, renders, or uses the result. The
report holds case IDs, categories, statuses, issue codes, and metrics only:
no transcript text, brief, clip title, or model output. The kit adds the
canary-leak scan over the report and the budget ledger.

Gate (held-out cases only; prompts may be tuned on dev cases only):

- every proposed clip starts and ends exactly on a transcript segment
  boundary (checked independently of the engine) — 100%;
- every proposed clip duration is inside the requested range — 100%;
- ambiguous briefs → ``needs_input`` — 100%;
- adversarial cases never pass silently — 0;
- structured cases use zero model calls;
- clear briefs yield a valid proposal (>= 95%), so a model that always
  fails cannot pass on structured clips alone;
- at most two model calls per job and zero canary leaks (kit checks).

Clear-brief exact range match is reported but not gated: the card's AC-VID-04a
gates alignment, duration, and abstention, and highlight choice is subjective.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brief_adapter_kit.shadow import composition, finalize_report, rate

from .brief import MAX_CLIP_SECONDS, MIN_CLIP_SECONDS
from .provider import HighlightProposer, HighlightResult

SUITE = "EVAL-VID-highlight"
CLEAR_PROPOSED_FLOOR = 0.95
_EPSILON = 1e-6


def expand_request(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the case request, appending ``pad_segments`` neutral filler segments if set."""

    request = dict(case["request"])
    pad = int(case.get("pad_segments", 0))
    if pad:
        segments = list(request["segments"])
        cursor = float(segments[-1]["end"])
        for number in range(pad):
            segments.append(
                {
                    "id": f"pad_{number:04d}",
                    "start": round(cursor, 3),
                    "end": round(cursor + 5.0, 3),
                    "text": f"filler line {number} with neutral words that only make the transcript long",
                }
            )
            cursor += 5.0
        request["segments"] = segments
    return request


def load_cases(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        for raw in json.loads(Path(path).read_text(encoding="utf-8")):
            cases.append({**raw, "request": expand_request(raw)})
    return cases


def load_split(path: str | Path | None) -> set[str]:
    if path is None:
        return set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return set(data.get("dev", []))


def _clip_checks(case: Mapping[str, Any], result: HighlightResult) -> tuple[int, int, int]:
    """Return (clips, boundary-aligned clips, in-range clips) checked against the raw case."""

    if result.proposal is None:
        return 0, 0, 0
    segments = {str(segment["id"]): segment for segment in case["request"]["segments"]}
    expected = case.get("expected", {})
    low = float(expected.get("min_seconds", MIN_CLIP_SECONDS))
    high = float(expected.get("max_seconds", MAX_CLIP_SECONDS))
    aligned = in_range = 0
    for clip in result.proposal.clips:
        first, last = segments.get(clip.start_segment_id), segments.get(clip.end_segment_id)
        if (
            first is not None
            and last is not None
            and clip.start == float(first["start"])
            and clip.end == float(last["end"])
        ):
            aligned += 1
        duration = clip.end - clip.start
        if low - _EPSILON <= duration <= high + _EPSILON:
            in_range += 1
    return len(result.proposal.clips), aligned, in_range


def _ranges(result: HighlightResult) -> list[list[str]]:
    if result.proposal is None:
        return []
    return [[clip.start_segment_id, clip.end_segment_id] for clip in result.proposal.clips]


def run_shadow(
    proposer: HighlightProposer,
    cases: list[dict[str, Any]],
    *,
    dev_ids: Iterable[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate ``proposer`` on ``cases`` and return a metadata-only shadow report."""

    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for case in cases:
        expected = case.get("expected", {})
        result = proposer.propose(case["request"], job_id=f"shadow-{stamp}-{case['id']}")
        clips, aligned, in_range = _clip_checks(case, result)
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "locale": case.get("locale", ""),
                "split": "dev" if case["id"] in dev else "held_out",
                "expected_status": expected.get("status"),
                "status": result.status,
                "route": result.route,
                "issue_codes": result.issue_codes,
                "missing": list(result.missing),
                "model_calls": result.model_calls,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "clips": clips,
                "clips_boundary_aligned": aligned,
                "clips_in_duration_range": in_range,
                "status_match": result.status == expected.get("status"),
                "range_match": bool(expected.get("ranges"))
                and _ranges(result) == expected.get("ranges"),
                "silent_pass": case["category"] == "adversarial" and result.status == "proposed",
            }
        )

    held = [row for row in rows if row["split"] == "held_out"]
    by_category = {
        name: [row for row in held if row["category"] == name]
        for name in ("structured", "clear", "ambiguous", "adversarial")
    }
    total_clips = sum(row["clips"] for row in held)
    alignment = rate(sum(row["clips_boundary_aligned"] for row in held), total_clips)
    duration = rate(sum(row["clips_in_duration_range"] for row in held), total_clips)
    recall = rate(
        sum(1 for row in by_category["ambiguous"] if row["status"] == "needs_input"),
        len(by_category["ambiguous"]),
    )
    structured_calls = sum(
        int(row["model_calls"]) for row in rows if row["category"] == "structured"
    )
    metrics: dict[str, Any] = {
        "cases": len(rows),
        "held_out_cases": len(held),
        "composition": composition(cases),
        "status_counts": {
            status: sum(1 for row in held if row["status"] == status)
            for status in ("proposed", "needs_input", "rejected")
        },
        "proposed_clips": total_clips,
        "boundary_alignment": alignment,
        "duration_in_range": duration,
        "needs_input_recall_ambiguous": recall,
        "adversarial_silent_passes": sum(
            1 for row in by_category["adversarial"] if row["silent_pass"]
        ),
        "structured_model_calls": structured_calls,
        "clear_proposed": rate(
            sum(1 for row in by_category["clear"] if row["status"] == "proposed"),
            len(by_category["clear"]),
        ),
        "clear_range_match": rate(
            sum(1 for row in by_category["clear"] if row["range_match"]), len(by_category["clear"])
        ),
        "expected_status_match": rate(sum(1 for row in held if row["status_match"]), len(held)),
        "model_calls_total": sum(int(row["model_calls"]) for row in rows),
        "input_tokens_total": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens_total": sum(int(row["output_tokens"]) for row in rows),
    }
    checks = {
        "boundary_alignment_100pct": alignment == 1.0,
        "duration_in_range_100pct": duration == 1.0,
        "needs_input_recall_100pct": recall == 1.0,
        "adversarial_silent_passes_zero": bool(by_category["adversarial"])
        and metrics["adversarial_silent_passes"] == 0,
        "structured_model_calls_zero": structured_calls == 0,
        "clear_proposed_ge_95pct": (metrics["clear_proposed"] or 0.0) >= CLEAR_PROPOSED_FLOOR,
    }
    return finalize_report(
        suite=SUITE,
        tool_id=proposer.config.tool_id,
        run_id=stamp,
        rows=rows,
        metrics=metrics,
        checks=checks,
        canaries=[str(case.get("canary", "")) for case in cases],
        ledger_path=proposer.ledger.path,
    )


__all__ = [
    "CLEAR_PROPOSED_FLOOR",
    "SUITE",
    "expand_request",
    "load_cases",
    "load_split",
    "run_shadow",
]
