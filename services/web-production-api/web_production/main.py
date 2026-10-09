"""Bounded local HTTP interface for Web V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, ReplayIntegrityError, run_page_job

app = FastAPI(title="Solo Empire Web Production API", version="0.1.0")


class PagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    page: dict[str, Any] = Field(min_length=1)


def _output_root() -> Path:
    return Path(os.environ.get("WEB_PRODUCTION_OUTPUT_ROOT", ".runtime/web-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "web-production-api", "contract_version": "web.v1a", "model_calls_supported": False, "deploy_supported": False}


@app.post("/v1/web/pages", status_code=201, response_model=None)
def create_page(payload: PagePayload) -> dict[str, Any]:
    try:
        result = run_page_job(payload.page, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except ReplayIntegrityError as exc:
        raise HTTPException(status_code=500, detail={"code": str(exc)}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
