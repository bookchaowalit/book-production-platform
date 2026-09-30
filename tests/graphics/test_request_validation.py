from __future__ import annotations

from typing import Any

from book_production_platform.graphics_ir import GraphicRequest, validate_request


def _request(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "request_id": "card-1",
        "template_id": "quote_card",
        "variant": "square",
        "canvas": {"width": 1080, "height": 1080},
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"quote": "ทำให้ชัดเจน", "author": "ทีมงาน"},
    }
    base.update(overrides)
    return base


def test_valid_mapping_and_model_have_no_issues() -> None:
    request = _request()

    assert validate_request(request) == []
    assert validate_request(GraphicRequest.model_validate(request)) == []


def test_schema_errors_become_invalid_request_issues_with_paths() -> None:
    issues = validate_request(_request(template_id="not-a-template"))

    assert issues
    assert {issue.code for issue in issues} == {"invalid_request"}
    assert any(issue.path == "template_id" for issue in issues)


def test_blank_request_id_and_empty_content_are_reported() -> None:
    issues = validate_request(_request(request_id="", content={}))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("request_id_required", "request_id"),
        ("content_required", "content"),
    ]


def test_blank_content_keys_and_values_are_reported() -> None:
    issues = validate_request(_request(content={" ": "text", "quote": "   "}))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("content_key_required", "content"),
        ("content_value_required", "content.quote"),
    ]


def test_validation_does_not_mutate_the_supplied_mapping() -> None:
    request = _request()
    snapshot = repr(request)

    validate_request(request)

    assert repr(request) == snapshot
