"""Bounded local HTTP interface for Graphics V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, run_batch

app = FastAPI(title="Book Production Platform Graphics API", version="0.1.0")


class BatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


def _output_root() -> Path:
    return Path(os.environ.get("GRAPHICS_PRODUCTION_OUTPUT_ROOT", ".runtime/graphics-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "graphics-production-api", "contract_version": "graphics.v1a", "model_calls_supported": False}


@app.post("/v1/graphics/batches", status_code=201, response_model=None)
def create_batch(payload: BatchPayload) -> dict[str, Any]:
    try:
        result = run_batch(payload.items, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
