"""Campaigns: plan validation, one look with a different layout per post, CSV input, parallel render, CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.graphics_production import image_fx
from book_production_platform.graphics_production.campaign import (
    CAMPAIGN_SCHEMA,
    CampaignError,
    load_campaign,
    run_campaign,
)
from book_production_platform.graphics_production.cli import main
from book_production_platform.graphics_production.layouts import (
    LAYOUT_DIR_ENV,
    recent_fingerprints,
)

PHOTOS = Path(__file__).parent / "fixtures" / "photos"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(image_fx, "ai_cutout_available", lambda: False)
    monkeypatch.setenv(LAYOUT_DIR_ENV, str(tmp_path / "layouts"))


@pytest.fixture
def plan_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "plan"
    (folder / "photos").mkdir(parents=True)
    for name in ("dog.png", "shop.jpg", "logo.png"):
        shutil.copy(PHOTOS / name, folder / "photos" / name)
    return folder


def plan(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": CAMPAIGN_SCHEMA, "campaign_id": "winter", "brand": {"primary": "#3B2A20", "accent": "#C9772E"},
        "variants": ["square"],
        "posts": [
            {"id": "p1", "content": {"headline": "Fresh drip", "cta": "Order"}, "photos": {"hero": "photos/shop.jpg"}},
            {"id": "p2", "content": {"headline": "ลาเต้ส้มยูซุ", "price": "95.-"}, "photos": {"hero": "photos/shop.jpg"}},
            {"id": "p3", "content": {"headline": "เปิด 7 โมงเช้าทุกวัน", "cta": "แวะมา"}},
            {"id": "p4", "content": {"headline": "Dogs welcome", "subhead": "Water and treats"}, "photos": {"hero": "photos/dog.png"}},
        ],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(("change", "code"), [
    ({"schema_version": "x"}, "schema_version"),
    ({"campaign_id": ""}, "campaign_id_required"),
    ({"variants": ["poster"]}, "unknown_variant"),
    ({"posts": []}, "no_posts"),
    ({"posts": [{"id": "a", "content": {"headline": "x", "slogan": "y"}}]}, "unknown_text_slot"),
    ({"posts": [{"id": "a", "content": {"cta": "y"}}]}, "headline_required"),
    ({"posts": [{"id": "a", "content": {"headline": "x"}, "photos": {"hero": "missing.jpg"}}]}, "photo_missing"),
    ({"posts": [{"id": "a", "content": {"headline": "x"}}, {"id": "a", "content": {"headline": "y"}}]}, "duplicate_post_id"),
    ({"posts": [{"id": "../a", "content": {"headline": "x"}}]}, "bad_post_id"),
])
def test_bad_plans_are_rejected(plan_dir: Path, change: dict[str, Any], code: str) -> None:
    with pytest.raises(CampaignError, match=code):
        load_campaign(plan(**change), plan_dir)


def test_every_post_gets_its_own_layout_in_one_look(plan_dir: Path, tmp_path: Path) -> None:
    summary = run_campaign(load_campaign(plan(art_style="jazz"), plan_dir), tmp_path / "out", seed=1, render=False)
    assert summary["composed"] == summary["total"] == 4 and summary["near_copy_pairs"] == 0
    ids = [option["layout_id"] for post in summary["posts"] for option in post["options"]]
    assert len(set(ids)) == 4 and len(recent_fingerprints()) == 4  # remembered, so the next job avoids them too


def test_posts_can_come_from_a_csv(plan_dir: Path, tmp_path: Path) -> None:
    (plan_dir / "posts.csv").write_text("id,headline,cta,photo:hero\nc1,Hot cocoa,Order,photos/shop.jpg\nc2,Closed Monday,,\n",
                                        encoding="utf-8")
    campaign = load_campaign(plan(posts=[], posts_csv="posts.csv"), plan_dir)
    assert [post.post_id for post in campaign.posts] == ["c1", "c2"] and "hero" in campaign.posts[0].photos
    (plan_dir / "bad.csv").write_text("id,headline,slogan\nc1,Hi,there\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="unknown_csv_column"):
        load_campaign(plan(posts=[], posts_csv="bad.csv"), plan_dir)


def test_cli_campaign_renders_posts_in_parallel(plan_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (plan_dir / "plan.json").write_text(json.dumps(plan()), encoding="utf-8")
    code = main(["compose-campaign", "--plan", str(plan_dir / "plan.json"), "--output", str(tmp_path / "out"),
                 "--layout-dir", str(tmp_path / "lib"), "--workers", "2"])
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["rendered"] == 4
    assert len(list((tmp_path / "out").glob("p*/*/*-square.png"))) == 4
    assert (tmp_path / "out" / "campaign-summary.json").is_file()


def test_one_logo_sits_in_the_same_corner_of_every_post(plan_dir: Path, tmp_path: Path) -> None:
    campaign = load_campaign(plan(logo="photos/logo.png", logo_position="bottom-left"), plan_dir)
    summary = run_campaign(campaign, tmp_path / "out", seed=2, render=False)
    assert summary["composed"] == summary["total"]

    for path in (tmp_path / "layouts").glob("lay_*.json"):
        spec = json.loads(path.read_text(encoding="utf-8"))
        box = next(e for e in spec["elements"] if e.get("id") == "logo")["boxes"]["square"]
        assert box["x"] < 0.5 and box["y"] > 0.5
    with pytest.raises(CampaignError, match="logo_position"):
        load_campaign(plan(logo="photos/logo.png", logo_position="middle"), plan_dir)
