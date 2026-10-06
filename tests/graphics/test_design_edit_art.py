"""EVAL-GFX-edit-art20: art style and photo edits, checked with an oracle and a degraded model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.brief_adapter_kit.shadow import composition
from book_production_platform.brief_adapter_kit.testing import ScriptedTransport
from book_production_platform.graphics_production.art_style_ai import ArtStyleDesigner
from book_production_platform.graphics_production.art_styles import (
    STYLE_DIR_ENV,
    get_art_style,
)
from book_production_platform.graphics_production.design_edit import (
    DesignEditor,
    apply_change,
)
from book_production_platform.graphics_production.design_edit_shadow import (
    run_edit_shadow,
)

FIXTURES = Path(__file__).parent / "fixtures"
CASES: list[dict[str, Any]] = json.loads((FIXTURES / "eval_gfx_edit_art20.json").read_text(encoding="utf-8"))
SPLIT = json.loads((FIXTURES / "eval_gfx_edit_art20_split.json").read_text(encoding="utf-8"))


def config(route: str) -> dict[str, Any]:
    return {
        "schema_version": "graphics-provider-config.v1",
        "mode": "shadow",
        "gateway_url": "https://gateway.example.test",
        "token_env": "GRAPHICS_TEST_TOKEN",
        "route": route,
        "prompt_version": "test",
        "max_token_budget": 10048,
        "rate_card": {"version": "test", "input_usd_per_1k": "0.001", "output_usd_per_1k": "0.002"},
        "caps": {"per_job_usd": "0.10", "per_day_usd": "5.00", "per_month_usd": "50.00"},
        "approval": {"approved_by": "test-owner", "approved_on": "2026-10-06"},
    }


def style_reply(payload: dict[str, Any]) -> dict[str, Any]:
    data = get_art_style("kawaii").to_json()
    for key in ("source", "schema_version"):
        data.pop(key)
    data.update(style_id="festival_look", title="Festival", evidence=["festival"])
    return data


def editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responder: Any) -> DesignEditor:
    monkeypatch.setenv(STYLE_DIR_ENV, str(tmp_path / "styles"))
    designer = ArtStyleDesigner(provider_config=config("graphics.art_style"), ledger_root=tmp_path / "style-budget",
                                transport=ScriptedTransport(responder=style_reply))
    return DesignEditor(provider_config=config("graphics.design_edit"), ledger_root=tmp_path / "budget",
                        transport=ScriptedTransport(responder=responder), style_designer=designer)


def oracle(payload: dict[str, Any]) -> Any:
    user = payload["messages"][1]["content"]
    return next(case["oracle_reply"] for case in CASES if case["canary"] in user)


def test_suite_composition_is_balanced() -> None:
    counts = composition(CASES)
    assert {category: sum(langs.values()) for category, langs in counts.items() if sum(langs.values())} == {
        "structured": 4, "clear": 8, "ambiguous": 4, "adversarial": 4, "new_look": 2,
    }
    assert all(langs.get("th", 0) == langs.get("en", 0) for langs in counts.values())
    assert set(SPLIT["dev"]) <= {case["id"] for case in CASES}


@pytest.mark.parametrize("case", [case for case in CASES if "patch" in case["request"]], ids=lambda case: case["id"])
def test_structured_cases_need_no_model(case: dict[str, Any]) -> None:
    result = apply_change(case["request"]["base"], case["request"]["patch"])
    assert result.status == case["expected"]["status"] and result.model_calls == 0


def test_oracle_passes_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = run_edit_shadow(editor(tmp_path, monkeypatch, oracle), CASES, dev_ids=SPLIT["dev"], suite=SPLIT["suite"])
    assert report["gate"]["passed"], (report["gate"], [row for row in report["cases"] if not row["status_match"] or row["exact"] is False])
    assert report["metrics"]["new_look_complete"] == 1.0
    assert all("CANARY" not in json.dumps(row) for row in report["cases"])


def test_a_model_that_ignores_looks_and_photos_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def degraded(payload: dict[str, Any]) -> Any:
        reply = oracle(payload)
        return {**reply, "art_style": "noir" if reply.get("art_style") else None, "images": {}, "missing": [], "new_art_style": None}

    report = run_edit_shadow(editor(tmp_path, monkeypatch, degraded), CASES, dev_ids=SPLIT["dev"], suite=SPLIT["suite"])
    assert not report["gate"]["passed"]
    assert not report["gate"]["checks"]["edit_exact_clear_ge_95pct"]


def test_a_look_named_in_the_theme_style_field_becomes_the_art_style() -> None:
    base = CASES[0]["request"]["base"]
    result = apply_change(base, {"style": "minimal"})
    assert result.status == "complete" and result.request is not None
    assert result.request["design"]["art_style"] == "minimal" and "style" not in result.request["design"]


def test_an_empty_reply_is_asked_again_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = {"style": None, "palette": {}, "layers": {}, "items": {}, "content": {}, "missing": [], "unsupported": []}
    replies = iter([empty, {**empty, "art_style": "jazz"}])
    result = editor(tmp_path, monkeypatch, lambda payload: next(replies)).edit(CASES[0]["request"]["base"], "ขอแนวแจ๊ส", job_id="empty-1")
    assert result.status == "complete" and result.model_calls == 2 and result.changed == ["art_style:jazz"]
