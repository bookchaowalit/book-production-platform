"""Trace V1B proposals and carry their AI metadata into the deterministic build.

``propose`` writes a proposal JSON; this module gives that run its own
``trace.json`` + history line (hashes and metadata only, never the brief), and
lets ``build --input <proposal.json>`` use the proposed request while recording
in the build trace that AI proposed it. It never imports the brief adapter kit.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from production_trace import TraceRecorder

from .jobs import RECIPE_VERSION, TOOL_ID

_PROPOSAL_KEYS = frozenset({"status", "request", "adapter", "model_calls"})


def split_proposal(payload: Any) -> tuple[Any, dict[str, Any] | None]:
    """Return ``(request, ai_metadata)``; a non-proposal payload is returned unchanged.

    A proposal that is not ``complete`` yields ``(None, metadata)`` so the
    caller refuses to build it.
    """

    if not isinstance(payload, Mapping) or not _PROPOSAL_KEYS <= set(payload):
        return payload, None
    metadata = {
        "model_calls": int(payload.get("model_calls") or 0),
        "adapter": str(payload.get("adapter", "unknown")),
        "mode": "live",
        "provided": [str(item) for item in payload.get("provided", []) or []],
        "inferred": [str(item) for item in payload.get("inferred", []) or []],
    }
    if payload.get("status") != "complete" or not isinstance(payload.get("request"), Mapping):
        return None, metadata
    return dict(payload["request"]), metadata


def trace_proposal(result: Mapping[str, Any], output: Path, idempotency_key: str, *, envelope: Any) -> dict[str, Any] | None:
    """Write ``<output dir>/propose-<key>/trace.json`` and append the history; never raises on I/O."""

    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    root = output.parent
    job_dir = root / f"propose-{key_hash}"
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        job_id=f"propose-{key_hash}",
        engine_version=str(result.get("adapter_version", "brief-adapter")),
        recipe_version=RECIPE_VERSION,
        schema_version="data-viz-proposal",
        idempotency_key_sha256=key_hash,
    )
    try:
        trace.input_json("envelope", envelope)
        trace.ai_proposal(
            model_calls=int(result.get("model_calls") or 0),
            adapter=str(result.get("adapter", "unknown")),
            mode="live",
            provided=[str(item) for item in result.get("provided", []) or []],
            inferred=[str(item) for item in result.get("inferred", []) or []],
        )
        trace.record_step(
            "ai",
            "propose_chart",
            outputs=["proposal.json"],
            status=str(result.get("status")),
            issue_codes=sorted({str(issue.get("code")) for issue in result.get("issues", []) or [] if isinstance(issue, Mapping)}),
            missing=[str(item) for item in result.get("missing", []) or []],
            input_tokens=int(result.get("input_tokens") or 0),
            output_tokens=int(result.get("output_tokens") or 0),
        )
        job_dir.mkdir(parents=True, exist_ok=True)
        frozen = job_dir / "proposal.json"
        shutil.copyfile(output, frozen)
        if isinstance(result.get("request"), Mapping):
            trace.ir_document(json.loads(json.dumps(result["request"], default=str)), schema="dataset-request.v1")
        trace.artifact(frozen, media_type="application/json", role="proposal")
        status = {"complete": "proposed"}.get(str(result.get("status")), str(result.get("status")))
        return trace.finish(status=status, job_dir=job_dir, output_root=root)
    except OSError:
        return None


__all__ = ["split_proposal", "trace_proposal"]
