from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from marketing_ir import compile_campaign, validate_request

from marketing_production.channel_tools import (
    build_tracked_url,
    experiments,
    hashtags_in,
    mentions_in,
    tag_checks,
    tracked_links,
)
from marketing_production.jobs import run_campaign_job


def _campaign(**extra: Any) -> dict[str, Any]:
    return {
        "campaign_id": "Spring Sale 2026",
        "name": "Spring",
        "objective": "conversion",
        "channels": ["instagram", "email"],
        "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "th"}]},
        "assets": [
            {"asset_id": "ig-a", "kind": "copy", "channel": "instagram", "uri": "inline:a", "text": "A #sale", "variant": "A"},
            {"asset_id": "ig-b", "kind": "copy", "channel": "instagram", "uri": "inline:b", "text": "B #sale", "variant": "B"},
            {"asset_id": "mail", "kind": "email", "channel": "email", "uri": "inline:m", "text": "Hello"},
        ],
        **extra,
    }


def test_hashtags_and_mentions_handle_thai_urls_and_emails() -> None:
    assert hashtags_in("ลอง #สวัสดีครับ #Sale2026, https://x.com/a#top &#35;") == ["สวัสดีครับ", "Sale2026"]
    assert mentions_in("hi @brand.th. mail a@b.com @x") == ["brand.th", "x"]


@pytest.mark.parametrize(
    ("channel", "text", "codes"),
    [
        ("instagram", " ".join(f"#t{i}" for i in range(31)), {"hashtag_limit_exceeded"}),
        ("instagram", " ".join(f"@u{i}" for i in range(21)), {"mention_limit_exceeded"}),
        ("instagram", " ".join(f"#t{i}" for i in range(30)), {"hashtags_above_recommended"}),
        ("facebook", "#Sale #sale", {"duplicate_hashtag"}),
        ("line", "ข่าวดี #news", {"hashtags_not_clickable"}),
        ("linkedin", "#a #b #c", set()),
    ],
)
def test_tag_checks(channel: str, text: str, codes: set[str]) -> None:
    assert {item["code"] for item in tag_checks(channel, text)} == codes


def test_build_tracked_url_keeps_query_and_fragment_and_encodes() -> None:
    url = build_tracked_url("https://shop.example/p?ref=1#buy", source="instagram", medium="social", campaign="spring sale", content="ig-a", term="shoes")
    assert url == "https://shop.example/p?ref=1&utm_source=instagram&utm_medium=social&utm_campaign=spring%20sale&utm_content=ig-a&utm_term=shoes#buy"


def test_tracked_links_and_experiments_from_ir() -> None:
    ir = compile_campaign(_campaign(utm={"landing_url": "https://shop.example/spring", "term": "shoes"}))
    links = tracked_links(ir)
    assert [(item["asset_id"], item["utm_source"], item["utm_medium"]) for item in links] == [
        ("ig-a", "instagram", "social"), ("ig-b", "instagram", "social"), ("mail", "newsletter", "email"),
    ]
    assert all("utm_campaign=spring-sale-2026" in item["url"] for item in links)
    assert experiments(ir) == [{"channel": "instagram", "kind": "copy", "variants": [{"variant": "A", "asset_id": "ig-a"}, {"variant": "B", "asset_id": "ig-b"}]}]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"utm": {"landing_url": "http://shop.example"}}, "unsafe_landing_url"),
        ({"utm": {"landing_url": "https://shop.example/?utm_source=x"}}, "landing_url_has_utm"),
    ],
)
def test_invalid_utm_is_rejected(change: dict[str, Any], code: str) -> None:
    assert [issue.code for issue in validate_request(_campaign(**change))] == [code]


def test_variant_rules() -> None:
    incomplete = _campaign()
    incomplete["assets"][1].pop("variant")
    assert [issue.code for issue in validate_request(incomplete)] == ["variant_group_incomplete"]
    duplicate = _campaign()
    duplicate["assets"][1]["variant"] = "A"
    assert [issue.code for issue in validate_request(duplicate)] == ["duplicate_variant"]
    single = _campaign()
    single["assets"] = [single["assets"][0], single["assets"][2]]
    assert [issue.code for issue in validate_request(single)] == ["single_variant"]


def test_job_package_carries_links_experiments_and_rejects_hard_tag_limits(tmp_path: Path) -> None:
    result = run_campaign_job(_campaign(utm={"landing_url": "https://shop.example/spring"}), tmp_path, "k-1")
    assert result.status == "accepted"
    too_many = _campaign()
    too_many["assets"][0]["text"] = " ".join(f"#t{i}" for i in range(31))
    rejected = run_campaign_job(too_many, tmp_path, "k-2")
    assert rejected.status == "rejected" and rejected.error_code == "hashtag_limit_exceeded"
