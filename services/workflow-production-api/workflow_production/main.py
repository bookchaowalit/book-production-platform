"""Bounded local HTTP interface for Workflow V1A."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import IdempotencyConflict, ReplayIntegrityError, run_workflow_job

app = FastAPI(title="Solo Empire Workflow Production API", version="0.1.0")


class WorkflowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    workflow: dict[str, Any] = Field(min_length=1)


def _output_root() -> Path:
    return Path(os.environ.get("WORKFLOW_PRODUCTION_OUTPUT_ROOT", ".runtime/workflow-production"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": "workflow-production-api",
        "contract_version": "workflow.v1a",
        "model_calls_supported": False,
        "external_effects_supported": False,
        "deploy_supported": False,
    }


@app.post("/v1/workflows/plans", status_code=201, response_model=None)
def create_workflow_plan(payload: WorkflowPayload) -> dict[str, Any]:
    try:
        result = run_workflow_job(payload.workflow, _output_root(), payload.idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except ReplayIntegrityError as exc:
        raise HTTPException(status_code=500, detail={"code": str(exc)}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
