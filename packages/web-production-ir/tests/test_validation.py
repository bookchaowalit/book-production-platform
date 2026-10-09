from __future__ import annotations

import pytest
from pydantic import ValidationError
from web_ir import PageIR, PageRequest, compile_page, validate_request


def _page(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "page_id": "launch-01",
        "slug": "new-launch",
        "title": "เปิดตัวสินค้าใหม่",
        "description": "หน้า landing สำหรับแคมเปญที่ตรวจสอบได้",
        "hero_heading": "ทำงานให้ชัดขึ้น",
        "hero_body": "ข้อความที่มี <script>alert('x')</script> ต้องแสดงเป็น text",
        "hero_cta": {"label": "เริ่มต้น", "href": "#signup"},
        "features": [
            {"title": "เร็ว", "body": "ทำงานด้วยกฎที่ตรวจซ้ำได้"},
            {"title": "ปลอดภัย", "body": "ไม่มี script หรือ external asset"},
        ],
        "footer_text": "Solo Empire",
        "theme": {"primary": "#2563EB", "background": "#FFFFFF", "foreground": "#111827"},
    }
    payload.update(overrides)
    return payload


def test_valid_page_compiles_to_fixed_template_and_keeps_text() -> None:
    request = PageRequest.model_validate(_page())

    issues = validate_request(request)
    ir = compile_page(request)

    assert issues == []
    assert isinstance(ir, PageIR)
    assert ir.template_ref == "landing-v1"
    assert ir.hero_body == _page()["hero_body"]
    assert ir.model_calls == 0


def test_english_locale_is_preserved_in_page_ir() -> None:
    ir = compile_page(_page(language="en"))

    assert ir.language == "en"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("slug", "Bad Slug", "invalid_request"),
        ("hero_cta", {"label": "Go", "href": "javascript:alert(1)"}, "unsafe_href"),
        ("hero_heading", "   ", "hero_heading_required"),
        ("features", [], "features_required"),
    ],
)
def test_invalid_page_returns_actionable_issue(field: str, value: object, code: str) -> None:
    payload = _page(**{field: value})

    issues = validate_request(payload)

    assert code in {issue.code for issue in issues}


def test_feature_limit_is_bounded() -> None:
    payload = _page(features=[{"title": str(index), "body": "body"} for index in range(7)])

    issues = validate_request(payload)

    assert any(issue.code == "invalid_request" and "features" in issue.path for issue in issues)


def test_unknown_raw_html_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PageRequest.model_validate(_page(raw_html="<script>bad</script>"))


def test_unsafe_href_is_rejected_for_secondary_cta() -> None:
    payload = _page(secondary_cta={"label": "External", "href": "https://example.com"})

    issues = validate_request(payload)

    assert any(issue.code == "unsafe_href" for issue in issues)
