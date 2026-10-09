"""Bounded local HTTP interface for Data + Data Viz V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, run_data_job

app = FastAPI(title="Solo Empire Data Production API", version="0.1.0")


class DataPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)
    request: dict[str, Any]


def _output_root() -> Path:
    return Path(os.environ.get("DATA_PRODUCTION_OUTPUT_ROOT", ".runtime/data-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "data-production-api", "contract_version": "data-v1a", "model_calls_supported": False}


@app.post("/v1/data/jobs", status_code=201, response_model=None)
def create_job(payload: DataPayload) -> dict[str, Any]:
    try:
        result = run_data_job(payload.request, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()


__all__ = ["app"]
