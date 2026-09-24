"""Loopback-only FastAPI interface for Data Pipeline Builder V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from book_production_platform.data_pipeline.jobs import (
    PipelineJobError,
    run_pipeline_job,
)
from book_production_platform.data_pipeline_ir.models import PipelineRequest
from book_production_platform.data_pipeline_ir.validation import (
    MAX_PIPELINE_REQUEST_BYTES,
)

app = FastAPI(title="Data SQL/Pipeline Builder V1A", version="0.1.0")


class PipelinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: PipelineRequest


def _output_root() -> Path:
    return Path(os.environ.get("DATA_PIPELINE_OUTPUT_ROOT", ".runtime/data-pipeline"))


@app.post("/v1/data/pipelines", status_code=200, response_model=None)
async def create_pipeline(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_PIPELINE_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail={"code": "pipeline_request_too_large"})
        body.extend(chunk)
    try:
        payload = PipelinePayload.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(status_code=422, detail={"code": "invalid_pipeline_request"}) from None

    try:
        result = run_pipeline_job(payload.pipeline, _output_root())
    except PipelineJobError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    if result.status == "rejected":
        status_code = 413 if result.error_code == "pipeline_request_too_large" else 422
        raise HTTPException(status_code=status_code, detail=result.to_dict())
    return result.to_dict()


__all__ = ["PipelinePayload", "app"]
