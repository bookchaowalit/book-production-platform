"""Job history, step trace, Webflow manual guide, and the parity features (pricing, FAQ, SEO, breakpoints)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from production_trace import read_history
from production_trace.cli import main as trace_main
from web_ir import compile_page

from web_production import jobs as jobs_module
from web_production.manual import alpha_rgba, build_webflow_guide, mix_hex
from web_production.provider import SECTION_TYPES, ground_page_proposal
from web_production.qa import run_page_qa
from web_production.render import BREAKPOINTS, render_css, render_html

CANARY = "CNRY-web-trace-7f3a"


def _page(**overrides: Any) -> dict[str, Any]:
    page: dict[str, Any] = {
        "page_id": "trace-01",
        "language": "en",
        "slug": "trace-page",
        "title": "Synthetic Bakery Nine",
        "description": "Fresh bread close to home",
        "hero_heading": "Bread before breakfast",
        "hero_body": f"Private brief detail {CANARY}",
        "hero_cta": {"label": "Order now", "href": "#signup"},
        "features": [{"title": "Sourdough", "body": "Slow-fermented loaves"}, {"title": "Delivery", "body": "Before work"}],
        "pricing": [
            {"name": "Starter", "price": "99 THB", "description": "One loaf a week", "features": ["1 loaf", "Pickup"]},
            {"name": "Family", "price": "299 THB", "features": ["4 loaves"]},
        ],
        "faq": [{"question": "Do you deliver?", "answer": "Yes, before 8 am."}],
        "secondary_cta": {"label": "Email us", "href": "mailto:hello@bakery.example"},
        "footer_text": "Synthetic Bakery Nine",
        "theme": {"primary": "#2563EB", "background": "#FFFFFF", "foreground": "#111827"},
    }
    page.update(overrides)
    return page


def _trace(root: Path, job_id: str) -> dict[str, Any]:
    return json.loads((root / "jobs" / job_id / "trace.json").read_text(encoding="utf-8"))


def test_accepted_job_writes_trace_ir_manual_and_history(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(), out, "key-1")

    assert result.status == "accepted"
    job_id = result.metadata["trace_job_id"]
    manifest = json.loads((out / str(result.manifest_filename)).read_text(encoding="utf-8"))
    assert manifest["metadata"]["trace_job_id"] == job_id
    job_dir = out / "jobs" / job_id
    assert {"trace.json", "ir.json", "MANUAL-STEPS.md"} <= {path.name for path in job_dir.iterdir()}
    trace = _trace(out, job_id)
    assert trace["tool_id"] == "web-v1a" and trace["status"] == "accepted"
    assert trace["ir"]["schema"] == "page-ir.v1" and trace["ir"]["file"] == "ir.json"
    assert [step["operation"] for step in trace["steps"]] == [
        "validate_request", "compile_page", "render_html", "render_css", "write_bundle", "run_page_qa", "write_manifest",
    ]
    render_step = trace["steps"][2]
    assert render_step["params"]["sections"] == ["hero", "features", "pricing", "faq", "cta", "footer"]
    assert {item["file"] for item in trace["artifacts"]} == {item.filename for item in result.artifacts}
    assert {item["sha256"] for item in trace["artifacts"]} == {item.content_hash for item in result.artifacts}
    assert trace["ai"] == {"used": False, "model_calls": 0}
    assert trace["manual_guide"]["program"] == "Webflow"
    history = read_history(out)
    assert [(entry["job_id"], entry["status"]) for entry in history] == [(job_id, "accepted")]
    assert history[0]["trace"] == f"jobs/{job_id}/trace.json"


def test_replay_adds_no_history_line(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = jobs_module.run_page_job(_page(), out, "key-1")
    replay = jobs_module.run_page_job(_page(), out, "key-1")

    assert replay.replayed is True
    assert replay.metadata["trace_job_id"] == first.metadata["trace_job_id"]
    assert len(read_history(out)) == 1


def test_trace_and_history_hold_no_private_copy(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(), out, "key-1")
    job_dir = out / "jobs" / result.metadata["trace_job_id"]

    assert CANARY not in (job_dir / "trace.json").read_text(encoding="utf-8")
    assert CANARY not in (out / "history.jsonl").read_text(encoding="utf-8")
    # The manual (same sensitivity as the page itself) does carry the copy a person must type.
    assert CANARY in (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")


def test_verify_passes_then_fails_after_tampering(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(), out, "key-1")
    job_id = result.metadata["trace_job_id"]

    assert trace_main(["verify", "--root", str(out), job_id]) == 0
    html = next(item.filename for item in result.artifacts if item.media_type == "text/html")
    delivered = out / html
    delivered.write_text(delivered.read_text(encoding="utf-8").replace("Bread", "Cake"), encoding="utf-8")
    capsys.readouterr()
    assert trace_main(["verify", "--root", str(out), job_id]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_rejected_request_is_traced(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(hero_cta={"label": "Go", "href": "https://evil.example"}), out, "key-bad")

    assert result.status == "rejected" and result.error_code == "unsafe_href"
    [entry] = read_history(out)
    assert entry["status"] == "rejected" and entry["artifacts"] == []
    trace = _trace(out, entry["job_id"])
    assert trace["steps"][-1] == {**trace["steps"][-1], "stage": "outcome", "operation": "rejected", "note": "unsafe_href"}


def test_schema_error_trace_never_echoes_input(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(slug=f"BAD {CANARY}"), out, "key-bad")

    assert result.status == "rejected"
    [entry] = read_history(out)
    text = (out / entry["trace"]).read_text(encoding="utf-8")
    assert CANARY not in text and '"note": "ValidationError"' in text


def test_qa_failure_is_traced_and_files_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    monkeypatch.setattr(jobs_module, "run_page_qa", lambda *args: ["unsafe_markup"])

    result = jobs_module.run_page_job(_page(), out, "key-qa")

    assert result.status == "rejected" and result.error_code == "unsafe_markup"
    assert not list(out.glob("*.html"))
    [entry] = read_history(out)
    trace = _trace(out, entry["job_id"])
    assert entry["status"] == "rejected"
    assert [step["status"] for step in trace["steps"] if step["stage"] == "qa"] == ["failed"]


def test_engine_crash_is_traced_and_still_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(jobs_module, "render_css", boom)
    with pytest.raises(RuntimeError, match="renderer exploded"):
        jobs_module.run_page_job(_page(), out, "key-crash")
    [entry] = read_history(out)
    trace = _trace(out, entry["job_id"])
    assert entry["status"] == "failed"
    assert [(step["operation"], step["status"]) for step in trace["steps"]][-2:] == [("render_css", "failed"), ("failed", "ok")]
    assert trace["steps"][-1]["note"] == "RuntimeError"


def test_ai_proposal_metadata_is_recorded(tmp_path: Path) -> None:
    out = tmp_path / "out"
    proposal = {"adapter": "web-provider-gateway", "mode": "live", "model_calls": 1, "provided": ["title", "features"], "inferred": ["hero", CANARY]}

    result = jobs_module.run_page_job(_page(), out, "key-ai", proposal=proposal)

    trace = _trace(out, result.metadata["trace_job_id"])
    assert trace["ai"] == {"used": True, "model_calls": 1, "adapter": "web-provider-gateway", "mode": "live", "provided": ["title", "features"], "inferred": ["hero"]}
    assert read_history(out)[0]["ai_used"] is True


def test_manual_guide_mirrors_every_element_of_the_ir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_page_job(_page(), out, "key-1")
    ir = compile_page(_page())
    manual = (out / "jobs" / result.metadata["trace_job_id"] / "MANUAL-STEPS.md").read_text(encoding="utf-8")

    expected = [
        ir.title, ir.description, ir.hero_heading, ir.hero_body, ir.hero_cta.label, ir.hero_cta.href, ir.footer_text,
        ir.theme.primary, ir.theme.background, ir.theme.foreground, ir.slug, "og:locale=en_US",
        mix_hex(ir.theme.primary, 0.12, ir.theme.background), alpha_rgba(ir.theme.foreground, 0.18),
        *(f"{width}px" for _, width in BREAKPOINTS), "Focused (keyboard)", "#FBBF24",
        "Features", "Pricing", "Frequently asked questions", "Ready to get started?",
    ]
    assert ir.secondary_cta is not None
    expected += [ir.secondary_cta.label, ir.secondary_cta.href]
    for feature in ir.features:
        expected += [feature.title, feature.body]
    for plan in ir.pricing:
        expected += [plan.name, plan.price, plan.description, *plan.features]
    for item in ir.faq:
        expected += [item.question, item.answer]
    missing = [value for value in expected if value and value not in manual]
    assert missing == []
    assert "Body ▸ Header.hero ▸ Div.container ▸ Heading 1" in manual
    assert "## 2. โครงสร้าง" in manual and "## 4. ตรวจผล" in manual
    assert "เวลาโดยประมาณ" in manual


def test_guide_layers_follow_render_order() -> None:
    ir = compile_page(_page())
    guide = build_webflow_guide(ir, html_name="p.html", css_name="p.css")
    html = render_html(ir, "p.css")
    texts = [layer.content for layer in guide.layers if layer.content and layer.kind not in ("Image",)]
    positions = [html.find(text.replace("&", "&amp;")) for text in texts if not text.startswith(("Features",))]
    assert all(position >= 0 for position in positions)
    assert len(guide.steps) > 40 and guide.estimated_minutes is not None


def test_mix_helpers_match_css_color_mix() -> None:
    assert mix_hex("#2563EB", 0.12, "#FFFFFF") == "#E5ECFD"
    assert alpha_rgba("#111827", 0.18) == "rgba(17, 24, 39, 0.18)"


def test_render_has_open_graph_pricing_faq_and_breakpoints() -> None:
    ir = compile_page(_page(faq=[{"question": "<b>Q</b>?", "answer": "A & B"}]))
    html = render_html(ir, "p.css")
    css = render_css(ir)

    assert '<meta property="og:title" content="Synthetic Bakery Nine">' in html
    assert '<meta property="og:locale" content="en_US">' in html
    assert '<meta name="twitter:card" content="summary">' in html
    assert html.index('id="features-title"') < html.index('id="pricing-title"') < html.index('id="faq-title"') < html.index('id="cta-title"')
    assert '<p class="price">99 THB</p>' in html and "<li>1 loaf</li>" in html
    assert "<summary>&lt;b&gt;Q&lt;/b&gt;?</summary><p>A &amp; B</p>" in html
    assert "repeat(2, minmax(0, 1fr)); gap: 1rem; }\n.pricing-card" in css
    for _, width in BREAKPOINTS:
        assert f"@media (max-width: {width}px)" in css
    assert run_page_qa(html, css, "p.css") == []


def test_page_without_optional_sections_omits_them() -> None:
    ir = compile_page(_page(pricing=[], faq=[]))
    html = render_html(ir, "p.css")
    assert 'id="pricing"' not in html and 'id="faq"' not in html
    assert run_page_qa(html, render_css(ir), "p.css") == []


def test_qa_flags_missing_seo_and_breakpoints() -> None:
    ir = compile_page(_page())
    html = render_html(ir, "p.css").replace('<meta property="og:title" content="Synthetic Bakery Nine">\n', "")
    css = render_css(ir).replace("@media (max-width: 991px)", "@media (max-width: 990px)")

    issues = run_page_qa(html, css, "p.css")

    assert "og_title_missing" in issues and "breakpoint_tablet_missing" in issues


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"pricing": [{"name": "", "price": "99 THB"}]}, "invalid_request"),
        ({"pricing": [{"name": "  ", "price": "99 THB"}]}, "pricing_plan_text_required"),
        ({"faq": [{"question": "Q?", "answer": "   "}]}, "faq_text_required"),
    ],
)
def test_blank_pricing_or_faq_is_rejected(tmp_path: Path, patch: dict[str, Any], code: str) -> None:
    result = jobs_module.run_page_job(_page(**patch), tmp_path / "out", "key-x")
    assert result.status == "rejected"
    assert code in str(result.error_code)


# --- V1B adapter registry stays in sync with the template --------------------------------------

BRIEF = (
    "Landing page for Synthetic Bakery Nine. Features: Sourdough loaves, Morning delivery. "
    "Plans: Starter 99 THB, Family 299 THB. We deliver before 8 am."
)


def _reply(**sections: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "title": "Synthetic Bakery Nine",
        "description": "A neighbourhood bakery",
        "sections": [
            {"type": "hero", "heading": "Fresh bread", "body": "Close to home", "cta_label": "Order now"},
            {"type": "features", "items": [{"title": "Sourdough loaves", "body": "Slow-fermented"}]},
        ],
        "provided": ["title", "features"],
        "inferred": ["description", "hero"],
        "missing": [],
    }
    reply["sections"] += [copy.deepcopy(section) for section in sections.values()]
    return reply


def test_adapter_registry_matches_template_sections() -> None:
    assert set(SECTION_TYPES) == {"hero", "features", "pricing", "faq", "contact", "footer"}


def test_adapter_accepts_grounded_pricing_and_faq() -> None:
    proposal = ground_page_proposal(
        _reply(
            pricing={"type": "pricing", "plans": [{"name": "Starter", "price": "99 THB", "features": ["Weekly loaf"]}]},
            faq={"type": "faq", "items": [{"question": "When do you deliver?", "answer": "Before 8 am."}]},
        ),
        BRIEF, page_id="bakery", locale="en-US",
    )

    assert proposal.status == "complete" and proposal.request is not None
    assert proposal.request["pricing"] == [{"name": "Starter", "price": "99 THB", "description": "", "features": ["Weekly loaf"]}]
    assert proposal.request["faq"] == [{"question": "When do you deliver?", "answer": "Before 8 am."}]
    assert "pricing" in proposal.provided and "faq" in proposal.inferred


@pytest.mark.parametrize(
    ("section", "missing"),
    [
        ({"type": "pricing", "plans": [{"name": "Starter", "price": "49 THB"}]}, "pricing"),
        ({"type": "pricing", "plans": [{"name": "Premium", "price": "99 THB"}]}, "pricing"),
        ({"type": "faq", "items": [{"question": "Is it cheap?", "answer": "Only 10 baht"}]}, "faq"),
        ({"type": "faq", "items": [{"question": "Why us?", "answer": "The best bakery in town"}]}, "faq"),
    ],
)
def test_adapter_never_invents_prices_or_facts(section: dict[str, Any], missing: str) -> None:
    proposal = ground_page_proposal(_reply(extra=section), BRIEF, page_id="bakery", locale="en-US")

    assert proposal.status == "needs_input"
    assert missing in proposal.missing


@pytest.mark.parametrize(
    ("section", "code"),
    [
        ({"type": "pricing", "plans": [{"name": "Starter", "price": "99 THB", "href": "/buy"}]}, "section_shape_invalid"),
        ({"type": "pricing", "plans": []}, "section_shape_invalid"),
        ({"type": "faq", "items": [{"question": "<script>x</script>", "answer": "a"}]}, "markup_forbidden"),
        ({"type": "faq", "items": [{"question": "Q", "answer": "a"}], "style": "x"}, "engine_field_forbidden"),
    ],
)
def test_adapter_rejects_malformed_pricing_and_faq(section: dict[str, Any], code: str) -> None:
    proposal = ground_page_proposal(_reply(extra=section), BRIEF, page_id="bakery", locale="en-US")

    assert proposal.status == "rejected"
    assert proposal.issues[0]["code"] == code
