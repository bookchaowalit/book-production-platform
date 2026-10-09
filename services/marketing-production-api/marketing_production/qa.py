"""Reopen and content checks for Marketing V1A package artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marketing_ir import CampaignIR
from pydantic import ValidationError


def run_package_qa(path: str | Path) -> list[str]:
    """Return stable QA issue codes for a serialized campaign package."""

    try:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["package_unreadable"]
    if not isinstance(payload, dict) or payload.get("schema_version") != "campaign-package.v1":
        return ["package_schema_invalid"]
    try:
        campaign = CampaignIR.model_validate(payload.get("campaign"))
    except ValidationError:
        return ["campaign_ir_invalid"]
    if campaign.publish_enabled or campaign.spend_enabled:
        return ["unsafe_policy"]
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict) or telemetry.get("model_calls") != 0:
        return ["model_calls_nonzero"]
    for asset in campaign.assets:
        lowered = asset.uri.lower()
        if lowered.startswith(("http://", "https://", "javascript:", "data:", "file://")):
            return ["unsafe_asset_uri"]
    return []
