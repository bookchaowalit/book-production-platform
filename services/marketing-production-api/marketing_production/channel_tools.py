"""Deterministic per-channel tools: hashtag/mention rules, UTM links, A/B groups.

These mirror what a person does in Meta Business Suite, LinkedIn, LINE OA
Manager, or an email tool before handing a post over for approval:

- **Hashtag and mention rules** (:func:`tag_checks`): Instagram refuses more
  than 30 hashtags or 20 mentions in one post (hard limit → the build is
  rejected); other findings are review flags (duplicate hashtags, hashtags on
  channels where they are not clickable, more than the recommended count).
- **UTM link builder** (:func:`tracked_links`): one tracked URL per copy,
  email, or landing-page asset, the same thing Google's Campaign URL Builder
  produces (``utm_source``/``utm_medium`` per channel, ``utm_campaign``,
  ``utm_content`` = asset id, optional ``utm_term``). Copy text is never
  rewritten; the link is a separate field to paste.
- **A/B groups** (:func:`experiments`): variants of one channel + kind.

Nothing here posts, schedules, sends, or spends.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from marketing_ir import CampaignIR

HASHTAG = re.compile(r"(?<![\w&])#([^\s#@,.!?;:()\[\]{}\"'<>/\\|]+)")
MENTION = re.compile(r"(?<![\w.@])@([A-Za-z0-9_][A-Za-z0-9_.]{0,59})")
HARD_TAG_LIMITS: dict[str, dict[str, int]] = {"instagram": {"hashtags": 30, "mentions": 20}}
RECOMMENDED_HASHTAGS: dict[str, int] = {"facebook": 5, "instagram": 15, "linkedin": 5}
HASHTAGS_NOT_CLICKABLE = frozenset(("email", "line", "web"))
UTM_SOURCE_MEDIUM: dict[str, tuple[str, str]] = {
    "facebook": ("facebook", "social"),
    "instagram": ("instagram", "social"),
    "linkedin": ("linkedin", "social"),
    "email": ("newsletter", "email"),
    "line": ("line", "messaging"),
    "web": ("website", "referral"),
}
LINKED_KINDS = frozenset(("copy", "email", "landing_page"))


def hashtags_in(text: str) -> list[str]:
    return HASHTAG.findall(unicodedata.normalize("NFC", text))


def mentions_in(text: str) -> list[str]:
    return [mention.rstrip(".") for mention in MENTION.findall(text)]


def tag_checks(channel: str, text: str) -> list[dict[str, str]]:
    """Return ``{"code", "severity"}`` findings; ``severity`` is ``error`` or ``review``."""

    tags = hashtags_in(text)
    mentions = mentions_in(text)
    findings: list[dict[str, str]] = []
    limits = HARD_TAG_LIMITS.get(channel, {})
    if len(tags) > limits.get("hashtags", 10**6):
        findings.append({"code": "hashtag_limit_exceeded", "severity": "error"})
    if len(mentions) > limits.get("mentions", 10**6):
        findings.append({"code": "mention_limit_exceeded", "severity": "error"})
    if len({tag.casefold() for tag in tags}) != len(tags):
        findings.append({"code": "duplicate_hashtag", "severity": "review"})
    if tags and channel in HASHTAGS_NOT_CLICKABLE:
        findings.append({"code": "hashtags_not_clickable", "severity": "review"})
    recommended = RECOMMENDED_HASHTAGS.get(channel)
    if recommended is not None and len(tags) > recommended and not any(item["code"] == "hashtag_limit_exceeded" for item in findings):
        findings.append({"code": "hashtags_above_recommended", "severity": "review"})
    return findings


def asset_checks(ir: CampaignIR) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for asset in ir.assets:
        if asset.text:
            checks += [{"asset_id": asset.asset_id, "channel": asset.channel, **finding} for finding in tag_checks(asset.channel, asset.text)]
    return checks


def build_tracked_url(landing_url: str, *, source: str, medium: str, campaign: str, content: str, term: str | None = None) -> str:
    """Append utm_* parameters (fixed order) after any existing query; keep the fragment."""

    parts = urlsplit(landing_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    utm = [("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign), ("utm_content", content)]
    if term:
        utm.append(("utm_term", term))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*query, *utm], quote_via=quote), parts.fragment))


def utm_campaign(ir: CampaignIR) -> str:
    if ir.utm is not None and ir.utm.campaign:
        return ir.utm.campaign
    return re.sub(r"[^a-z0-9._-]+", "-", ir.campaign_id.lower()).strip("-") or "campaign"


def tracked_links(ir: CampaignIR) -> list[dict[str, str]]:
    if ir.utm is None:
        return []
    links = []
    for asset in ir.assets:
        if asset.kind not in LINKED_KINDS:
            continue
        source, medium = UTM_SOURCE_MEDIUM[asset.channel]
        url = build_tracked_url(ir.utm.landing_url, source=source, medium=medium, campaign=utm_campaign(ir), content=asset.asset_id, term=ir.utm.term)
        links.append({"asset_id": asset.asset_id, "channel": asset.channel, "utm_source": source, "utm_medium": medium, "url": url})
    return links


def experiments(ir: CampaignIR) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for asset in ir.assets:
        if asset.variant is not None:
            groups.setdefault((asset.channel, asset.kind), []).append({"variant": asset.variant, "asset_id": asset.asset_id})
    return [
        {"channel": channel, "kind": kind, "variants": sorted(members, key=lambda item: item["variant"])}
        for (channel, kind), members in sorted(groups.items())
    ]


__all__ = [
    "HARD_TAG_LIMITS",
    "RECOMMENDED_HASHTAGS",
    "UTM_SOURCE_MEDIUM",
    "asset_checks",
    "build_tracked_url",
    "experiments",
    "hashtags_in",
    "mentions_in",
    "tag_checks",
    "tracked_links",
    "utm_campaign",
]
