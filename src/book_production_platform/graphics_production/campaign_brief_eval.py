"""EVAL-GFX-campaign-brief10: how faithfully a client brief becomes a campaign plan.

Each case is a brief, the photo files the owner provided, and the expected
result: status, the posts in order with the exact texts per slot, the photos per
post, and (when the brief gives them) brand colours, art style, sizes, and logo.
Rows hold ids, categories, statuses, and numbers only (no brief text).

Gate (held-out cases): completion >= 85% of normal cases, post-count exact >= 85%,
text exact (every expected slot with exactly the brief's text) >= 90%, zero
invented texts (guaranteed by the grounding check, re-measured here), zero
adversarial silent passes, at most two calls per brief, zero canary leaks.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import finalize_report, rate

from .campaign_brief import CampaignBriefAdapter, _norm

SUITE = "EVAL-GFX-campaign-brief10"
TOOL_ID = "graphics-v1a"


def run_campaign_brief_eval(adapter: CampaignBriefAdapter, cases: list[dict[str, Any]], *, dev_ids: Iterable[str] = (),
                            run_id: str | None = None, suite: str = SUITE) -> dict[str, Any]:
    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        result = adapter.propose(case["brief"], job_id=f"campaign-brief-eval-{stamp}-{case['id']}", photos=list(case["photos"]),
                                 photos_prefix="", locale=case.get("locale", "th-TH"))
        plan = result.plan or {}
        posts = plan.get("posts", [])
        want = expected.get("posts", [])
        count_ok = len(posts) == len(want)
        slots_total = slots_exact = invented = photo_total = photo_ok = 0
        source = _norm(case["brief"])
        for post in posts:
            for text in post.get("content", {}).values():
                invented += int(_norm(text) not in source)
        for got, wanted in zip(posts, want, strict=False):
            for slot, text in wanted.get("content", {}).items():
                slots_total += 1
                slots_exact += int(_norm(got.get("content", {}).get(slot, "")) == _norm(text))
            for key, name in wanted.get("photos", {}).items():
                photo_total += 1
                photo_ok += int(got.get("photos", {}).get(key) == name)
        slots_total += sum(len(p.get("content", {})) for p in want[len(posts):])
        extras = {
            key: plan.get(key) == expected[key] for key in ("art_style", "logo", "variants") if key in expected
        }
        if "brand" in expected:
            extras["brand"] = (plan.get("brand") or None) == (expected["brand"] or None)
        rows.append({
            "id": case["id"], "category": case["category"], "locale": case.get("locale", ""),
            "split": "dev" if case["id"] in dev else "held_out", "status": result.status,
            "expected_status": expected.get("status", "complete"), "posts": len(posts), "expected_posts": len(want),
            "post_count_exact": count_ok, "slots_exact": slots_exact, "slots_total": slots_total, "invented_texts": invented,
            "photos_exact": photo_ok, "photos_total": photo_total, "extras_exact": sum(extras.values()), "extras_total": len(extras),
            "questions": len(result.questions), "model_calls": result.model_calls,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "silent_pass": case["category"] == "adversarial" and result.status == "complete",
        })
    held = [row for row in rows if row["split"] == "held_out"]
    normal = [row for row in held if row["expected_status"] == "complete"]
    slots_total = sum(row["slots_total"] for row in normal)
    metrics = {
        "cases": len(rows), "held_out": len(held),
        "completion": rate(sum(1 for row in normal if row["status"] == "complete"), len(normal)),
        "post_count_exact": rate(sum(1 for row in normal if row["post_count_exact"]), len(normal)),
        "text_exact": rate(sum(row["slots_exact"] for row in normal), slots_total) if slots_total else 1.0,
        "photo_exact": rate(sum(row["photos_exact"] for row in normal), sum(row["photos_total"] for row in normal) or 1),
        "settings_exact": rate(sum(row["extras_exact"] for row in normal), sum(row["extras_total"] for row in normal) or 1),
        "invented_texts": sum(row["invented_texts"] for row in held),
        "expected_status_match": rate(sum(1 for row in held if row["status"] == row["expected_status"]), len(held)),
        "adversarial_silent_passes": sum(1 for row in held if row["silent_pass"]),
        "model_calls_total": sum(row["model_calls"] for row in held),
        "input_tokens_total": sum(row["input_tokens"] for row in held),
        "output_tokens_total": sum(row["output_tokens"] for row in held),
    }
    checks = {
        "completion_ge_85pct": metrics["completion"] >= 0.85,
        "post_count_exact_ge_85pct": metrics["post_count_exact"] >= 0.85,
        "text_exact_ge_90pct": metrics["text_exact"] >= 0.90,
        "invented_texts_zero": metrics["invented_texts"] == 0,
        "adversarial_silent_passes_zero": metrics["adversarial_silent_passes"] == 0,
    }
    return finalize_report(suite=suite, tool_id=TOOL_ID, run_id=stamp, rows=rows, metrics=metrics, checks=checks,
                           canaries=[str(case.get("canary", "")) for case in cases], ledger_path=adapter.runner.ledger.path)


__all__ = ["SUITE", "run_campaign_brief_eval"]
