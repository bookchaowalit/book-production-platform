from __future__ import annotations

from pathlib import Path

import pytest
from marketing_ir import CampaignIR, CampaignRequest, compile_campaign, validate_request
from pydantic import ValidationError


def _request(asset_root: str | None = None) -> dict:
    payload = {
        "campaign_id": "thai-launch-01",
        "name": "เปิดตัวสินค้าใหม่",
        "objective": "conversion",
        "channels": ["facebook", "email"],
        "audience": {
            "name": "ลูกค้าเดิม",
            "rules": [
                {"field": "language", "operator": "equals", "value": "th"},
            ],
        },
        "assets": [
            {
                "asset_id": "email-copy",
                "kind": "copy",
                "channel": "email",
                "uri": "inline:email-copy",
                "text": "=โปรพิเศษสำหรับสมาชิก",
            },
            {
                "asset_id": "social-image",
                "kind": "image",
                "channel": "facebook",
                "uri": "creative.png",
            },
        ],
    }
    if asset_root is not None:
        payload["asset_root"] = asset_root
    return payload


def test_valid_campaign_compiles_in_deterministic_order_and_keeps_copy_as_text(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"png")
    request = CampaignRequest.model_validate(_request(str(tmp_path)))

    issues = validate_request(request)
    ir = compile_campaign(request)

    assert issues == []
    assert isinstance(ir, CampaignIR)
    assert [asset.asset_id for asset in ir.assets] == ["email-copy", "social-image"]
    assert ir.assets[0].text == "=โปรพิเศษสำหรับสมาชิก"
    assert ir.publish_enabled is False
    assert ir.spend_enabled is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("channels", ["facebook", "facebook"], "duplicate_channel"),
        ("assets", [{"asset_id": "same", "kind": "image", "channel": "facebook", "uri": "inline:a"}, {"asset_id": "same", "kind": "copy", "channel": "email", "uri": "inline:b", "text": "b"}], "duplicate_asset_id"),
        ("assets", [{"asset_id": "bad", "kind": "image", "channel": "facebook", "uri": "https://example.com/a.png"}, {"asset_id": "email", "kind": "copy", "channel": "email", "uri": "inline:e", "text": "e"}], "unsafe_asset_uri"),
    ],
)
def test_invalid_campaign_returns_stable_issue_codes(field: str, value: object, code: str, tmp_path: Path) -> None:
    payload = _request(str(tmp_path))
    payload[field] = value

    issues = validate_request(payload)

    assert code in {issue.code for issue in issues}


def test_asset_path_traversal_is_rejected(tmp_path: Path) -> None:
    payload = _request(str(tmp_path))
    payload["assets"][1]["uri"] = "../outside.png"

    issues = validate_request(payload)

    assert any(issue.code == "asset_path_outside_root" for issue in issues)


def test_channels_must_have_assets(tmp_path: Path) -> None:
    payload = _request(str(tmp_path))
    payload["assets"] = [payload["assets"][1]]
    payload["assets"][0]["channel"] = "facebook"

    issues = validate_request(payload)

    assert any(issue.code == "channel_without_asset" and issue.path.endswith("email") for issue in issues)


def test_missing_required_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignRequest.model_validate({"campaign_id": "x"})


def test_blank_asset_id_is_rejected() -> None:
    payload = _request()
    payload["assets"][0]["asset_id"] = "   "

    issues = validate_request(payload)

    assert any(issue.code == "asset_id_required" for issue in issues)


def test_absolute_asset_uri_is_normalized_without_private_root(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"png")
    payload = _request(str(tmp_path))
    payload["assets"][1]["uri"] = str(tmp_path / "creative.png")

    ir = compile_campaign(payload)

    image = next(asset for asset in ir.assets if asset.asset_id == "social-image")
    assert image.uri == "creative.png"
    assert str(tmp_path) not in " ".join(ir.source_refs)


def test_variants_and_utm_are_validated_and_compiled() -> None:
    payload = _request()
    payload["channels"] = ["email"]
    payload["assets"] = [
        {"asset_id": "a", "kind": "copy", "channel": "email", "uri": "inline:a", "text": "A", "variant": "A"},
        {"asset_id": "b", "kind": "copy", "channel": "email", "uri": "inline:b", "text": "B", "variant": "B"},
    ]
    payload["utm"] = {"landing_url": "https://example.com/x"}
    assert validate_request(payload) == []
    ir = compile_campaign(payload)
    assert ir.utm is not None and ir.utm.landing_url == "https://example.com/x"
    assert [asset.variant for asset in ir.assets] == ["A", "B"]
    payload["assets"][1]["variant"] = None
    assert [issue.code for issue in validate_request(payload)] == ["variant_group_incomplete"]
    payload["assets"][1]["variant"] = "B"
    payload["utm"] = {"landing_url": "javascript:alert(1)//x"}
    assert [issue.code for issue in validate_request(payload)] == ["unsafe_landing_url"]
    with pytest.raises(ValidationError):
        CampaignRequest.model_validate({**payload, "assets": [{**payload["assets"][0], "variant": "ab"}]})
