"""Bounded local HTTP interface for Marketing V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, ReplayIntegrityError, run_campaign_job

app = FastAPI(title="Solo Empire Marketing Production API", version="0.1.0")


class CampaignPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    campaign: dict[str, Any] = Field(min_length=1)


def _output_root() -> Path:
    return Path(os.environ.get("MARKETING_PRODUCTION_OUTPUT_ROOT", ".runtime/marketing-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": "marketing-production-api",
        "contract_version": "marketing.v1a",
        "model_calls_supported": False,
        "publish_supported": False,
        "spend_supported": False,
    }


@app.post("/v1/marketing/campaigns", status_code=201, response_model=None)
def create_campaign(payload: CampaignPayload) -> dict[str, Any]:
    try:
        result = run_campaign_job(payload.campaign, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except ReplayIntegrityError as exc:
        raise HTTPException(status_code=500, detail={"code": str(exc)}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
