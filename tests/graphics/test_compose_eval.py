"""EVAL-GFX-compose14: fixture shape, procedural baseline gate, and a scripted art-director run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.brief_adapter_kit.testing import ScriptedTransport
from book_production_platform.graphics_production import image_fx
from book_production_platform.graphics_production.cli import main
from book_production_platform.graphics_production.compose_eval import run_compose_eval
from book_production_platform.graphics_production.composer import plan_layout
from book_production_platform.graphics_production.composer_ai import ArtDirector
from book_production_platform.graphics_production.layouts import LAYOUT_DIR_ENV

FIXTURES = Path(__file__).parent / "fixtures"
CASES = FIXTURES / "eval_gfx_compose14.json"
SPLIT = FIXTURES / "eval_gfx_compose14_split.json"
PHOTOS = FIXTURES / "photos"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)
    monkeypatch.setenv(LAYOUT_DIR_ENV, str(tmp_path / "real-layouts"))


def cases() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(CASES.read_text(encoding="utf-8"))
    return loaded


def test_fixture_covers_languages_photos_and_adversarial_cases() -> None:
    loaded = cases()
    assert len(loaded) == 14 and len({case["id"] for case in loaded}) == 14
    assert {case["locale"][:2] for case in loaded} == {"th", "en"}
    assert sum(1 for case in loaded if case["category"] == "adversarial") == 2
    assert any(len(case["request"].get("assets", {})) >= 2 for case in loaded)
    assert all(case["canary"] in case["request"]["content"]["footer"] for case in loaded)
    for case in loaded:
        for name in case["request"].get("assets", {}).values():
            assert (PHOTOS / name).is_file()


def test_procedural_baseline_passes_the_gate_and_keeps_rows_text_free(tmp_path: Path) -> None:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    report = run_compose_eval(cases(), photo_root=PHOTOS, dev_ids=split["dev"], run_id="t")
    assert report["gate"]["passed"], report["gate"]
    metrics = report["metrics"]["procedural"]
    assert metrics["completion"] == 1.0 and metrics["near_copy_pair_rate"] <= 0.1 and metrics["mean_score"] >= 70
    assert not any(row["category"] == "adversarial" for row in report["cases"])  # never read text as instructions
    serialized = json.dumps(report, ensure_ascii=False)
    assert "น้องหมา" not in serialized and "Fresh drip" not in serialized
    assert not (tmp_path / "real-layouts").exists()  # evaluation never writes the real layout memory


def test_scripted_director_is_measured_next_to_the_baseline(tmp_path: Path) -> None:
    picked = [case for case in cases() if case["id"] in {"cp-phot-th-01", "cp-type-en-04", "cp-adve-en-13"}]
    replies = []
    for case, archetype in zip([c for c in picked if c["category"] != "adversarial"], ["circle_focus", "type_poster"], strict=True):
        request = case["request"]
        spec = plan_layout(request["content"], sorted(request.get("assets") or {}), case["variants"], request["brand"], 3, archetype)
        replies.append({key: spec[key] for key in ("concept", "palette", "background", "elements")})
    config = {
        "schema_version": "graphics-provider-config.v1", "mode": "shadow", "gateway_url": "https://gateway.example.test",
        "token_env": "GRAPHICS_TEST_TOKEN", "route": "graphics.art_director", "prompt_version": "test", "max_token_budget": 10048,
        "rate_card": {"version": "test", "input_usd_per_1k": "0.001", "output_usd_per_1k": "0.002"},
        "caps": {"per_job_usd": "0.10", "per_day_usd": "5.00", "per_month_usd": "50.00"},
        "approval": {"approved_by": "test-owner", "approved_on": "2026-10-06"},
    }
    director = ArtDirector(provider_config=config, ledger_root=tmp_path / "budget", transport=ScriptedTransport(*replies))
    report = run_compose_eval(picked, photo_root=PHOTOS, director=director, run_id="t")
    rows = {(row["id"], row["mode"]): row for row in report["cases"]}
    assert rows[("cp-phot-th-01", "director")]["status"] == "complete" and rows[("cp-phot-th-01", "director")]["score"] > 0
    assert rows[("cp-adve-en-13", "director")]["status"] == "needs_input"
    assert report["metrics"]["director"]["adversarial_silent_passes"] == 0
    assert report["metrics"]["max_model_calls_per_job"] <= 2 and report["metrics"]["canary_leaks"] == 0


def test_cli_compose_shadow_writes_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["compose-shadow", "--cases", str(CASES), "--split", str(SPLIT), "--photo-root", str(PHOTOS),
                 "--output", str(tmp_path / "report.json")])
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["gate"]["passed"]
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["suite"] == "EVAL-GFX-compose14"
