"""Pure validation and compilation for structured Web V1A page requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .models import LinkSpec, PageIR, PageRequest, ValidationIssue


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def _check_text(value: str, path: str, code: str) -> ValidationIssue | None:
    if not value.strip():
        return _issue(code, path, f"{path} is required")
    return None


def _check_href(link: LinkSpec, path: str) -> ValidationIssue | None:
    href = link.href.strip()
    lowered = href.lower()
    if lowered.startswith(("javascript:", "data:", "http://", "https:", "//")):
        return _issue("unsafe_href", f"{path}.href", "href must be a relative path, fragment, or mailto link")
    if not (href.startswith(("/", "#")) or lowered.startswith("mailto:")):
        return _issue("unsupported_href", f"{path}.href", "href must be a relative path, fragment, or mailto link")
    return None


def validate_request(request: Mapping[str, Any] | PageRequest) -> list[ValidationIssue]:
    """Return stable, actionable issues without mutating the supplied value."""

    if isinstance(request, PageRequest):
        parsed = request
    else:
        try:
            parsed = PageRequest.model_validate(request)
        except ValidationError as exc:
            return [
                _issue("invalid_request", ".".join(str(part) for part in error["loc"]) or "$", str(error["msg"]))
                for error in exc.errors()
            ]

    issues: list[ValidationIssue] = []
    for field, code in (("page_id", "page_id_required"), ("slug", "slug_required"), ("title", "title_required"), ("description", "description_required"), ("hero_heading", "hero_heading_required"), ("hero_body", "hero_body_required")):
        issue = _check_text(getattr(parsed, field), field, code)
        if issue is not None:
            issues.append(issue)
    for path, link in (("hero_cta", parsed.hero_cta), ("secondary_cta", parsed.secondary_cta)):
        if link is not None:
            issue = _check_text(link.label, f"{path}.label", "cta_label_required")
            if issue is not None:
                issues.append(issue)
            href_issue = _check_href(link, path)
            if href_issue is not None:
                issues.append(href_issue)
    if not parsed.features:
        issues.append(_issue("features_required", "features", "at least one feature is required"))
    for index, feature in enumerate(parsed.features):
        title_issue = _check_text(feature.title, f"features.{index}.title", "feature_title_required")
        body_issue = _check_text(feature.body, f"features.{index}.body", "feature_body_required")
        if title_issue is not None:
            issues.append(title_issue)
        if body_issue is not None:
            issues.append(body_issue)
    for index, plan in enumerate(parsed.pricing):
        for path, value in ((f"pricing.{index}.name", plan.name), (f"pricing.{index}.price", plan.price)):
            issue = _check_text(value, path, "pricing_plan_text_required")
            if issue is not None:
                issues.append(issue)
        for item_index, item in enumerate(plan.features):
            issue = _check_text(item, f"pricing.{index}.features.{item_index}", "pricing_feature_text_required")
            if issue is not None:
                issues.append(issue)
    for index, faq in enumerate(parsed.faq):
        for path, value in ((f"faq.{index}.question", faq.question), (f"faq.{index}.answer", faq.answer)):
            issue = _check_text(value, path, "faq_text_required")
            if issue is not None:
                issues.append(issue)
    return issues


def compile_page(request: Mapping[str, Any] | PageRequest) -> PageIR:
    """Validate and compile a fixed landing template IR."""

    parsed = request if isinstance(request, PageRequest) else PageRequest.model_validate(request)
    issues = validate_request(parsed)
    if issues:
        raise ValueError("invalid_request:" + ",".join(issue.code for issue in issues))
    return PageIR(
        page_id=parsed.page_id,
        language=parsed.language,
        slug=parsed.slug,
        title=parsed.title,
        description=parsed.description,
        hero_heading=parsed.hero_heading,
        hero_body=parsed.hero_body,
        hero_cta=parsed.hero_cta,
        features=parsed.features,
        pricing=parsed.pricing,
        faq=parsed.faq,
        secondary_cta=parsed.secondary_cta,
        footer_text=parsed.footer_text,
        theme=parsed.theme,
        hero_image=parsed.hero_image,
        source_refs=[parsed.slug],
    )
