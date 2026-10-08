"""Review page and feedback loop: page lists every option, verdicts are stored, rejected ideas rank lower."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_production_platform.graphics_production import image_fx
from book_production_platform.graphics_production.cli import main
from book_production_platform.graphics_production.composer import compose
from book_production_platform.graphics_production.layouts import LAYOUT_DIR_ENV
from book_production_platform.graphics_production.review import (
    build_review_page,
    feedback_report,
    idea_penalties,
    import_feedback,
)

PHOTOS = Path(__file__).parent / "fixtures" / "photos"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)
    monkeypatch.setenv(LAYOUT_DIR_ENV, str(tmp_path / "layouts"))


def test_compose_output_gets_a_review_page_with_every_option_and_size(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    code = main(["compose", "--set", "headline=Fresh drip coffee", "--set", "cta=Order", "--photo", f"hero={PHOTOS / 'dog.png'}",
                 "--variant", "square", "--variant", "story", "--count", "2", "--seed", "3", "--layout-dir", str(tmp_path / "layouts"),
                 "--output", str(out), "--idempotency-key", "review-1"])
    assert code == 0
    summary = json.loads((out / "compose-summary.json").read_text(encoding="utf-8"))
    assert all(option["archetype"] and option["score"] for option in summary["options"])
    capsys.readouterr()
    assert main(["review", "--dir", str(out)]) == 0
    page = (out / "review.html").read_text(encoding="utf-8")
    assert page.count("<article") == 2 and page.count("<img") == 4 and "review-feedback.json" in page
    assert build_review_page(tmp_path / "empty").read_text(encoding="utf-8").count("<article") == 0


def test_feedback_is_stored_and_ideas_people_reject_rank_lower(tmp_path: Path) -> None:
    items = [{"layout_id": f"lay_{i}", "archetype": "grid", "score": "90", "verdict": "fail", "reasons": ["empty", "bogus"]}
             for i in range(3)]
    items += [{"layout_id": "lay_ok", "archetype": "split", "score": "80", "verdict": "pass", "reasons": []},
              {"layout_id": "lay_skip", "archetype": "split", "verdict": None}]
    assert import_feedback({"items": items}, reviewer="owner") == 4
    assert idea_penalties() == {"grid": 15.0}  # split has fewer than 3 verdicts: no opinion yet
    report = feedback_report()
    assert report["fail"] == 3 and report["high_score_fails"] == 3 and report["reasons"] == [("empty", 3)]
    base = {"request_id": "x", "brand": {"primary": "#0F4C5C", "accent": "#F2A541"},
            "content": {"headline": "Grooming week", "cta": "Book"}, "assets": {"hero": "dog.png"}, "asset_root": str(PHOTOS)}
    picked = compose(base, ["square"], count=4, seed=9, avoid_history=False)
    assert "grid" not in [spec["archetype"] for spec in picked.specs[:2]]  # a rejected idea no longer leads the options


def test_cli_feedback_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exported = tmp_path / "review-feedback.json"
    exported.write_text(json.dumps({"schema_version": "graphic-review.v1", "items": [
        {"layout_id": "lay_a", "archetype": "sandwich", "score": "88.5", "verdict": "pass", "reasons": [], "note": "ดีมาก"}]}),
        encoding="utf-8")
    assert main(["feedback", "--file", str(exported), "--layout-dir", str(tmp_path / "layouts")]) == 0
    assert json.loads(capsys.readouterr().out)["stored"] == 1
    assert main(["feedback-report", "--layout-dir", str(tmp_path / "layouts")]) == 0
    assert json.loads(capsys.readouterr().out)["pass"] == 1
