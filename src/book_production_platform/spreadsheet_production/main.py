"""Bounded local HTTP interface for Spreadsheet V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, run_workbook_job

app = FastAPI(title="Solo Empire Spreadsheet Production API", version="0.1.0")


class WorkbookPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)
    request: dict[str, Any]


def _output_root() -> Path:
    return Path(os.environ.get("SPREADSHEET_PRODUCTION_OUTPUT_ROOT", ".runtime/spreadsheet-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "spreadsheet-production-api", "contract_version": "spreadsheet.v1a", "model_calls_supported": False}


@app.post("/v1/spreadsheets/workbooks", status_code=201, response_model=None)
def create_workbook(payload: WorkbookPayload) -> dict[str, Any]:
    try:
        result = run_workbook_job(payload.request, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    if result.status != "accepted":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
