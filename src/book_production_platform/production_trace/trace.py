"""Per-job production trace: what the engine did, step by step, with hashes.

Every production tool records one ``production-trace.v1`` document per job in
the job directory (``trace.json``) and appends one metadata line to the output
root's ``history.jsonl``. Together they answer "what was produced, from which
inputs, by which engine and recipe, through which steps, and was AI involved"
for any past job, and let a job be re-checked byte for byte.

The trace holds names, parameters, hashes, sizes, and timings only. It never
holds raw briefs, transcripts, or private document text; content that a person
needs to reproduce the job by hand lives in the manual guide next to the
artifacts (same sensitivity as the artifacts themselves).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .guide import ManualGuide, render_manual_markdown

TRACE_SCHEMA = "production-trace.v1"
HISTORY_FILENAME = "history.jsonl"
TRACE_FILENAME = "trace.json"
MANUAL_FILENAME = "MANUAL-STEPS.md"
_MAX_PARAM_TEXT = 300


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_param(value: Any) -> Any:
    """Keep parameters small and structural; long text is replaced by its hash."""

    if isinstance(value, str):
        if len(value) > _MAX_PARAM_TEXT:
            return {"sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(), "chars": len(value)}
        return value
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_param(item) for key, item in list(value.items())[:100]}
    if isinstance(value, list | tuple):
        return [_safe_param(item) for item in list(value)[:200]]
    return str(value)[:_MAX_PARAM_TEXT]


@dataclass
class TraceStep:
    index: int
    stage: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    status: str = "ok"
    started_at: str = ""
    duration_ms: float = 0.0
    note: str = ""


class TraceRecorder:
    """Collect one job's inputs, steps, artifacts, and outcome, then persist them."""

    def __init__(
        self,
        *,
        tool_id: str,
        job_id: str | None = None,
        engine_version: str,
        recipe_version: str,
        schema_version: str | None = None,
        idempotency_key_sha256: str | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.job_id = job_id or str(uuid.uuid4())
        self.engine_version = engine_version
        self.recipe_version = recipe_version
        self.schema_version = schema_version
        self.idempotency_key_sha256 = idempotency_key_sha256
        self.created_at = _now()
        self._started = time.perf_counter()
        self.inputs: list[dict[str, Any]] = []
        self.steps: list[TraceStep] = []
        self.artifacts: list[dict[str, Any]] = []
        self.ir: dict[str, Any] | None = None
        self.ai: dict[str, Any] = {"used": False, "model_calls": 0}
        self.qa: dict[str, Any] = {}

    # --- inputs -----------------------------------------------------------------
    def input_file(self, name: str, path: str | Path, *, role: str = "source") -> None:
        file = Path(path)
        self.inputs.append({"name": name, "role": role, "file": file.name, "bytes": file.stat().st_size, "sha256": sha256_file(file)})

    def input_json(self, name: str, value: Any, *, role: str = "request") -> None:
        self.inputs.append({"name": name, "role": role, "sha256": sha256_json(value)})

    def ir_document(self, value: Any, *, schema: str, path: str | Path | None = None) -> None:
        """Record the validated IR (the exact recipe the engine executed)."""

        self.ir = {"schema": schema, "sha256": sha256_json(value)}
        if path is not None:
            self.ir["file"] = Path(path).name

    def ai_proposal(self, *, model_calls: int, adapter: str, mode: str, provided: list[str] | None = None, inferred: list[str] | None = None) -> None:
        self.ai = {
            "used": model_calls > 0,
            "model_calls": model_calls,
            "adapter": adapter,
            "mode": mode,
            "provided": list(provided or []),
            "inferred": list(inferred or []),
        }

    # --- steps ------------------------------------------------------------------
    @contextmanager
    def step(self, stage: str, operation: str, **params: Any) -> Iterator[TraceStep]:
        record = TraceStep(index=len(self.steps) + 1, stage=stage, operation=operation, params=_safe_param(params), started_at=_now())
        started = time.perf_counter()
        self.steps.append(record)
        try:
            yield record
        except BaseException as exc:
            record.status = "failed"
            record.note = type(exc).__name__
            raise
        finally:
            record.duration_ms = round((time.perf_counter() - started) * 1000, 3)

    def record_step(self, stage: str, operation: str, *, outputs: list[str] | None = None, note: str = "", **params: Any) -> TraceStep:
        record = TraceStep(
            index=len(self.steps) + 1, stage=stage, operation=operation, params=_safe_param(params),
            outputs=list(outputs or []), started_at=_now(), note=note,
        )
        self.steps.append(record)
        return record

    # --- outputs ----------------------------------------------------------------
    def artifact(self, path: str | Path, *, media_type: str = "", role: str = "output") -> None:
        file = Path(path)
        self.artifacts.append({"file": file.name, "role": role, "media_type": media_type, "bytes": file.stat().st_size, "sha256": sha256_file(file)})

    def to_dict(self, *, status: str) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA,
            "tool_id": self.tool_id,
            "job_id": self.job_id,
            "status": status,
            "created_at": self.created_at,
            "finished_at": _now(),
            "wall_seconds": round(time.perf_counter() - self._started, 6),
            "engine_version": self.engine_version,
            "recipe_version": self.recipe_version,
            "request_schema": self.schema_version,
            "idempotency_key_sha256": self.idempotency_key_sha256,
            "inputs": self.inputs,
            "ir": self.ir,
            "ai": self.ai,
            "steps": [asdict(step) for step in self.steps],
            "artifacts": self.artifacts,
            "qa": self.qa,
        }

    def finish(
        self,
        *,
        status: str,
        job_dir: str | Path,
        output_root: str | Path,
        guide: ManualGuide | None = None,
        qa: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write ``trace.json`` (+ ``MANUAL-STEPS.md``) atomically and append the history index."""

        if qa is not None:
            self.qa = {"status": qa.get("status"), "checks": len(qa.get("checks", []) or qa.get("issues", []) or [])}
        directory = Path(job_dir)
        directory.mkdir(parents=True, exist_ok=True)
        trace = self.to_dict(status=status)
        if guide is not None:
            manual = directory / MANUAL_FILENAME
            _atomic_write(manual, render_manual_markdown(guide, trace))
            trace["manual_guide"] = {"file": MANUAL_FILENAME, "program": guide.program, "steps": len(guide.steps), "layers": len(guide.layers)}
        _atomic_write(directory / TRACE_FILENAME, json.dumps(trace, ensure_ascii=False, indent=2) + "\n")
        append_history(output_root, trace, trace_path=directory / TRACE_FILENAME)
        return trace


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def append_history(output_root: str | Path, trace: Mapping[str, Any], *, trace_path: str | Path) -> None:
    """Append one metadata-only line to ``<output_root>/history.jsonl``."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        relative = str(Path(trace_path).resolve().relative_to(root.resolve()))
    except ValueError:
        relative = str(trace_path)
    entry = {
        "schema_version": "production-history.v1",
        "tool_id": trace["tool_id"],
        "job_id": trace["job_id"],
        "status": trace["status"],
        "finished_at": trace["finished_at"],
        "recipe_version": trace["recipe_version"],
        "ai_used": bool(trace.get("ai", {}).get("used")),
        "artifacts": [artifact["file"] for artifact in trace.get("artifacts", [])],
        "trace": relative,
    }
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with (root / HISTORY_FILENAME).open("a", encoding="utf-8") as handle:
        handle.write(line)


def read_history(output_root: str | Path) -> list[dict[str, Any]]:
    path = Path(output_root) / HISTORY_FILENAME
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def verify_trace(output_root: str | Path, job_id: str) -> dict[str, Any]:
    """Re-hash every recorded artifact of a past job and report drift."""

    root = Path(output_root)
    entry = next((item for item in reversed(read_history(root)) if item["job_id"] == job_id), None)
    if entry is None:
        return {"job_id": job_id, "found": False}
    trace_path = root / entry["trace"]
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    results = []
    for artifact in trace.get("artifacts", []):
        candidate = trace_path.parent / artifact["file"]
        actual = sha256_file(candidate) if candidate.is_file() else None
        results.append({"file": artifact["file"], "ok": actual == artifact["sha256"], "missing": actual is None})
    return {"job_id": job_id, "found": True, "ok": all(item["ok"] for item in results), "artifacts": results}


__all__ = [
    "HISTORY_FILENAME",
    "MANUAL_FILENAME",
    "TRACE_FILENAME",
    "TRACE_SCHEMA",
    "TraceRecorder",
    "TraceStep",
    "append_history",
    "read_history",
    "sha256_file",
    "sha256_json",
    "verify_trace",
]
