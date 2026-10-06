"""Campaigns: many posts in one run, one look, a different composition for every post.

A campaign plan names the brand, an optional art style, the sizes, and the posts
(texts and photos per post), either inline or as a CSV. Every post is composed
in order against the layout memory, so each post avoids the layouts of the
posts before it (and of earlier jobs); the shared brand colors and art style
keep the series recognisably one campaign. Rendering is the slow part, so posts
render in parallel worker processes, each as its own idempotent batch.

Plan (``graphic-campaign.v1``)::

    {"schema_version": "graphic-campaign.v1", "campaign_id": "winter-2026",
     "brand": {"primary": "#3B2A20", "accent": "#C9772E"}, "art_style": "jazz",
     "variants": ["square", "story"], "options_per_post": 1,
     "posts": [{"id": "p01", "content": {"headline": "..."}, "photos": {"hero": "photos/cup.jpg"}}]}

or ``"posts_csv": "posts.csv"`` with one row per post: an ``id`` column, text
columns named after the slots (``headline``, ``cta``, ...), and photo columns
``photo:<key>``. Photo paths are relative to the plan file.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .composer import compose
from .jobs import run_batch
from .layouts import TEXT_SLOTS, save_layout, similarity
from .themes import VARIANT_SIZES

CAMPAIGN_SCHEMA = "graphic-campaign.v1"
MAX_POSTS = 200


class CampaignError(ValueError):
    """Raised for a plan that cannot run (bad schema, unknown slot or size, missing photo)."""


@dataclass
class Post:
    post_id: str
    content: dict[str, str]
    photos: dict[str, Path]


@dataclass
class Campaign:
    campaign_id: str
    brand: dict[str, str]
    variants: list[str]
    posts: list[Post]
    art_style: str | None = None
    options_per_post: int = 1
    meta: dict[str, Any] = field(default_factory=dict)


def _post(post_id: str, content: Mapping[str, Any], photos: Mapping[str, Any], root: Path) -> Post:
    if not post_id or not post_id.replace("-", "").replace("_", "").isalnum():
        raise CampaignError(f"bad_post_id:{post_id!r}")
    texts = {str(slot): str(text).strip() for slot, text in content.items() if str(text).strip()}
    unknown = sorted(set(texts) - set(TEXT_SLOTS))
    if unknown:
        raise CampaignError(f"unknown_text_slot:{post_id}:{','.join(unknown)}")
    if not texts.get("headline") and not texts.get("big"):
        raise CampaignError(f"headline_required:{post_id}")
    files: dict[str, Path] = {}
    for key, value in photos.items():
        if not value:
            continue
        path = (root / str(value)).resolve()
        if not key.replace("_", "").isalnum() or not path.is_file():
            raise CampaignError(f"photo_missing:{post_id}:{key}")
        files[key] = path
    return Post(post_id, texts, files)


def load_campaign(plan: Mapping[str, Any], plan_dir: str | Path) -> Campaign:
    """Validate a plan; photo and CSV paths resolve against ``plan_dir``."""

    root = Path(plan_dir)
    if plan.get("schema_version") != CAMPAIGN_SCHEMA:
        raise CampaignError(f"schema_version must be {CAMPAIGN_SCHEMA}")
    campaign_id = str(plan.get("campaign_id", "")).strip()
    if not campaign_id or not campaign_id.replace("-", "").replace("_", "").isalnum():
        raise CampaignError("campaign_id_required")
    variants = list(plan.get("variants") or ["portrait"])
    if "all" in variants:
        variants = list(VARIANT_SIZES)
    if any(variant not in VARIANT_SIZES for variant in variants):
        raise CampaignError(f"unknown_variant:{variants}")
    posts: list[Post] = []
    for raw in plan.get("posts") or []:
        posts.append(_post(str(raw.get("id", "")), raw.get("content") or {}, raw.get("photos") or {}, root))
    if plan.get("posts_csv"):
        with (root / str(plan["posts_csv"])).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                content = {key: value for key, value in row.items() if key in TEXT_SLOTS}
                photos = {key.split(":", 1)[1]: value for key, value in row.items() if key and key.startswith("photo:")}
                extra = sorted(key for key in row if key and key != "id" and key not in TEXT_SLOTS and not key.startswith("photo:"))
                if extra:
                    raise CampaignError(f"unknown_csv_column:{','.join(extra)}")
                posts.append(_post(str(row.get("id", "")).strip(), content, photos, root))
    if not posts:
        raise CampaignError("no_posts")
    if len(posts) > MAX_POSTS:
        raise CampaignError(f"too_many_posts:{len(posts)}>{MAX_POSTS}")
    if len({post.post_id for post in posts}) != len(posts):
        raise CampaignError("duplicate_post_id")
    options = int(plan.get("options_per_post", 1))
    if not 1 <= options <= 4:
        raise CampaignError("options_per_post must be 1-4")
    brand = {key: str(value) for key, value in (plan.get("brand") or {}).items()}
    return Campaign(campaign_id, brand, variants, posts, plan.get("art_style") or None, options)


def _render(items: list[dict[str, Any]], output: str, key: str) -> dict[str, Any]:
    result = run_batch(items, output, key)
    return {"status": result.status, "manifest": str(result.manifest_path) if result.manifest_path else None}


def run_campaign(campaign: Campaign, output_dir: str | Path, *, seed: int = 0, workers: int | None = None,
                 render: bool = True) -> dict[str, Any]:
    """Compose every post (in order, with memory), then render the posts in parallel; returns a summary."""

    output = Path(output_dir)
    posts: list[dict[str, Any]] = []
    jobs: list[tuple[str, list[dict[str, Any]], str, str]] = []
    delivered: list[tuple[str, list[str]]] = []
    for index, post in enumerate(campaign.posts):
        base: dict[str, Any] = {"request_id": post.post_id, "brand": dict(campaign.brand), "content": dict(post.content)}
        if post.photos:
            root = Path(os.path.commonpath([str(path.parent) for path in post.photos.values()]))
            base.update(asset_root=str(root), assets={key: path.relative_to(root).as_posix() for key, path in post.photos.items()})
        if campaign.art_style:
            base["design"] = {"art_style": campaign.art_style}
        result = compose(base, campaign.variants, count=campaign.options_per_post, seed=seed * 1000 + index)
        summary: dict[str, Any] = {"id": post.post_id, "options": [], "rejected": result.rejected}
        if not result.specs:
            summary["status"] = "no_layout"
            posts.append(summary)
            continue
        items = []
        for number, (spec, prints, score) in enumerate(zip(result.specs, result.fingerprints, result.scores, strict=True), start=1):
            save_layout(spec, prints)
            delivered.extend(prints.items())
            summary["options"].append({"layout_id": spec["layout_id"], "archetype": spec.get("archetype"), "score": round(score, 1)})
            for variant in campaign.variants:
                width, height = VARIANT_SIZES[variant]
                items.append({**base, "request_id": f"{post.post_id}-option{number}-{variant}", "template_id": spec["layout_id"],
                              "variant": variant, "canvas": {"width": width, "height": height}})
        summary["status"] = "composed"
        posts.append(summary)
        jobs.append((post.post_id, items, str(output / post.post_id), f"{campaign.campaign_id}-{post.post_id}"))
    if render and jobs:
        count = max(1, min(workers or os.cpu_count() or 1, len(jobs)))
        by_id = {post["id"]: post for post in posts}
        if count == 1:
            for post_id, items, folder, key in jobs:
                by_id[post_id]["batch"] = _render(items, folder, key)
        else:
            with ProcessPoolExecutor(max_workers=count) as pool:
                futures = {post_id: pool.submit(_render, items, folder, key) for post_id, items, folder, key in jobs}
                for post_id, future in futures.items():
                    by_id[post_id]["batch"] = future.result()
    pairs = [(a, b) for index, a in enumerate(delivered) for b in delivered[index + 1:] if a[0] == b[0]]
    near = sum(1 for (_va, fa), (_vb, fb) in pairs if similarity(fa, fb) >= 0.8)
    archetypes = [option["archetype"] for post in posts for option in post["options"]]
    scores = [option["score"] for post in posts for option in post["options"]]
    return {
        "campaign_id": campaign.campaign_id, "posts": posts,
        "composed": sum(1 for post in posts if post["status"] == "composed"), "total": len(posts),
        "rendered": sum(1 for post in posts if post.get("batch", {}).get("status") in {"accepted", "partial"}),
        "distinct_ideas": len(set(archetypes)), "mean_score": round(sum(scores) / len(scores), 1) if scores else None,
        "near_copy_pairs": near, "compared_pairs": len(pairs),
    }


__all__ = ["CAMPAIGN_SCHEMA", "Campaign", "CampaignError", "Post", "load_campaign", "run_campaign"]
