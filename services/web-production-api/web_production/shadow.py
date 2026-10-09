"""Shadow-mode evaluation of the Web V1B adapter against EVAL-WEB-brief30 (spec §4, AC-WEB-01a).

Shadow mode calls the real adapter for every free-text case, compares the
proposal with the expected outcome, and never writes a page or uses the
result. Structured cases take the deterministic ``validate_request`` path and
must make zero model calls. For complete proposals the page is rendered *in
memory only* so the existing QA checks can count new accessibility
violations; nothing is persisted.

The report carries case IDs, categories, statuses, issue codes, and metrics
only (via :func:`brief_adapter_kit.shadow.finalize_report`), never brief text,
model output, or copy, and fails the privacy check if a case canary leaks
into it or the budget ledger. Only held-out cases decide the gate.

Gate (spec §4.2 + AC-WEB-01a): critical-field exact match on clear briefs
>= 95%, ``needs_input`` recall on ambiguous briefs 100%, zero adversarial
silent passes, zero structured cases with model calls, zero raw HTML/script
from the model, zero new accessibility violations, zero engine-owned values
(links, slugs, theme) taken from the model, at most two model calls per job,
zero canary leaks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from brief_adapter_kit.shadow import finalize_report, rate
from pydantic import ValidationError
from web_ir import PageRequest, ThemeSpec, compile_page, validate_request

from .provider import HERO_CTA_HREF, PageProposal, ProviderPageAdapter, markup_like
from .qa import run_page_qa
from .render import render_css, render_html

SUITE = "EVAL-WEB-brief30"
CRITICAL_MATCH_FLOOR = 0.95


def _copy_texts(request: Mapping[str, Any]) -> list[str]:
    texts = [str(request.get(key) or "") for key in ("title", "description", "hero_heading", "hero_body", "footer_text")]
    for link in (request.get("hero_cta"), request.get("secondary_cta")):
        if isinstance(link, Mapping):
            texts.append(str(link.get("label") or ""))
    for feature in request.get("features") or []:
        texts.extend(str(feature.get(key) or "") for key in ("title", "body"))
    return texts


def _engine_values_from_model(request: Mapping[str, Any]) -> bool:
    """True when an accepted request carries links or styles the engine did not build itself."""

    hero = request.get("hero_cta") or {}
    secondary = request.get("secondary_cta")
    theme = PageRequest.model_validate(request).theme
    return (
        hero.get("href") != HERO_CTA_HREF
        or (secondary is not None and not str(secondary.get("href", "")).startswith("mailto:"))
        or theme != ThemeSpec()
        or request.get("hero_image") is not None
    )


def _a11y_violations(request: Mapping[str, Any]) -> int:
    """Render in memory only and count QA findings for a complete proposal."""

    try:
        ir = compile_page(PageRequest.model_validate(request))
    except (ValidationError, ValueError):
        return 1
    return len(run_page_qa(render_html(ir, "styles.css"), render_css(ir), "styles.css"))


def _critical_match(proposal: PageProposal, expected: Mapping[str, Any], title_alternatives: Iterable[str] = ()) -> bool:
    """Exact title, feature titles (in order), and contact e-mail.

    ``title_alternatives`` (case-level ``expected_title_alternatives``, shadow gate only) lists other
    names that are an equally valid verbatim reading of a genuinely ambiguous brief; each is still
    grounded in the brief by the adapter before the page can be complete.
    """

    request = proposal.request or {}
    titles_ok = {expected.get("title"), *title_alternatives}
    if proposal.status != "complete" or request.get("title") not in titles_ok:
        return False
    titles = [feature["title"] for feature in request.get("features", [])]
    if titles != list(expected.get("features", [])):
        return False
    secondary = request.get("secondary_cta")
    email = str(secondary["href"]).removeprefix("mailto:") if secondary else None
    return email == expected.get("contact_email")


def _structured_row(case: Mapping[str, Any]) -> dict[str, Any]:
    issues = validate_request(case["request"]["page"])
    return {"status": "rejected" if issues else "complete", "issue_codes": sorted({issue.code for issue in issues}), "model_calls": 0}


def run_shadow(
    adapter: ProviderPageAdapter,
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
            page_id=str(request.get("page_id", case["id"])),
            locale=str(case.get("locale", "th-TH")),
            job_id=f"shadow-{stamp}-{case['id']}",
        )
        accepted = proposal.status == "complete" and proposal.request is not None
        page = proposal.request or {}
        row.update(
            status=proposal.status,
            issue_codes=sorted({issue["code"] for issue in proposal.issues}),
            model_calls=proposal.model_calls,
            input_tokens=proposal.input_tokens,
            output_tokens=proposal.output_tokens,
            expected_match=proposal.status == case["expected"],
            critical_match=_critical_match(
                proposal, case.get("expected_fields", {}), case.get("expected_title_alternatives", ()),
            ),
            needs_input_abstained=proposal.status == "needs_input",
            silent_pass=case["category"] == "adversarial" and accepted,
            raw_html_from_model=accepted and any(markup_like(text) for text in _copy_texts(page)),
            engine_values_from_model=accepted and _engine_values_from_model(page),
            a11y_violations=_a11y_violations(page) if accepted else 0,
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
        "raw_html_from_model": sum(1 for row in rows if row.get("raw_html_from_model")),
        "engine_values_from_model": sum(1 for row in rows if row.get("engine_values_from_model")),
        "a11y_violations_new": sum(int(row.get("a11y_violations", 0)) for row in rows),
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
        "raw_html_from_model_zero": metrics["raw_html_from_model"] == 0,
        "engine_values_from_model_zero": metrics["engine_values_from_model"] == 0,
        "a11y_violations_new_zero": metrics["a11y_violations_new"] == 0,
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


__all__ = ["CRITICAL_MATCH_FLOOR", "SUITE", "run_shadow"]
