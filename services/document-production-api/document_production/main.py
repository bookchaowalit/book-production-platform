"""Bounded local HTTP interface for Documents V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from document_ir import DocumentRequest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .extraction import BriefRequest
from .jobs import (
    IdempotencyConflict,
    JobResult,
    run_document_brief_job,
    run_document_job,
)
from .recipe import RECIPE_IDS

app = FastAPI(title="Solo Empire Documents V1A", version="0.1.0")


class JobPayload(DocumentRequest):
    idempotency_key: str


class BriefJobPayload(BriefRequest):
    idempotency_key: str


def _output_root() -> Path:
    return Path(os.environ.get("DOCUMENT_PRODUCTION_OUTPUT_ROOT", ".runtime/document-production"))


def _public_result(result: JobResult) -> dict[str, Any]:
    payload = result.to_dict()
    if result.manifest_path is not None:
        payload["manifest_path"] = result.manifest_path.name
    return payload


@app.get("/health")
def health() -> dict[str, Any]:
    return {"service": "document-production-api", "contract_version": "documents.v1a", "model_calls_supported": False}


@app.post("/v1/documents/jobs", status_code=201, response_model=None)
def create_document_job(payload: JobPayload) -> dict[str, Any] | JSONResponse:
    request = payload.model_dump(exclude={"idempotency_key"})
    try:
        result = run_document_job(
            request, _output_root(), payload.idempotency_key,
            recipe_version=RECIPE_IDS.get(str(request.get("template_id")), RECIPE_IDS["invoice"]),
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": str(exc)}) from exc
    if result.status == "pending":
        return JSONResponse(status_code=202, content=_public_result(result), headers={"Retry-After": "1"})
    if result.status != "accepted":
        status_code = 422 if result.error_code in {"RequestValidationError", "IdempotencyConflict"} else 500
        raise HTTPException(status_code=status_code, detail={"code": result.error_code or "job_failed", "qa": result.qa})
    return _public_result(result)


@app.post("/v1/documents/briefs", status_code=201, response_model=None)
def create_document_brief_job(payload: BriefJobPayload) -> dict[str, Any] | JSONResponse:
    request = payload.model_dump(exclude={"idempotency_key"})
    try:
        result = run_document_brief_job(request, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": str(exc)}) from exc
    if result.status == "needs_input":
        return JSONResponse(status_code=422, content=_public_result(result))
    if result.status == "pending":
        return JSONResponse(status_code=202, content=_public_result(result), headers={"Retry-After": "1"})
    if result.status != "accepted":
        raise HTTPException(status_code=500, detail={"code": result.error_code or "job_failed", "qa": result.qa})
    return _public_result(result)
