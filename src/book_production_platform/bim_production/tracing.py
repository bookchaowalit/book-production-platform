"""Where BIM takeoff and cost jobs keep their production trace.

The content-addressed job directory (``<output_root>/jobs/<fingerprint>/``) must hold
exactly the three verified artifacts, so the per-run trace lives beside it under
``<output_root>/.trace/``:

* ``.trace/history.jsonl`` - one metadata line per run (accepted, replayed, rejected)
* ``.trace/jobs/<job_id>/trace.json`` - inputs, steps, artifacts (sha256), QA
* ``.trace/jobs/<job_id>/MANUAL-STEPS.md`` - the Bonsai / spreadsheet recipe
* ``.trace/jobs/<job_id>/ir.json`` (the takeoff request)

Artifacts outside the trace directory are recorded with a path relative to it,
so ``python -m book_production_platform.production_trace verify --root
<output_root>/.trace <job_id>`` re-hashes the published files themselves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from book_production_platform.production_trace import TraceRecorder, sha256_file

TRACE_DIRNAME = ".trace"
TRACE_JOBS_DIRNAME = "jobs"


def trace_root(output_root: Path) -> Path:
    return output_root / TRACE_DIRNAME


def trace_job_dir(output_root: Path, job_id: str) -> Path:
    return trace_root(output_root) / TRACE_JOBS_DIRNAME / job_id


def record_artifact(
    trace: TraceRecorder,
    path: Path,
    *,
    job_dir: Path,
    media_type: str = "",
    role: str = "output",
) -> None:
    """Record ``path`` (size + sha256) relative to the trace job directory."""

    relative = Path(os.path.relpath(path, job_dir)).as_posix()
    trace.artifacts.append(
        {
            "file": relative,
            "role": role,
            "media_type": media_type,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    )


def record_source_file(trace: TraceRecorder, name: str, path: Path, *, role: str = "source") -> None:
    """Record a source file by size and hash only; its local name is never stored."""

    trace.inputs.append(
        {"name": name, "role": role, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    )


def write_ir_file(job_dir: Path, value: Any) -> Path:
    path = job_dir / "ir.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return path


__all__ = [
    "TRACE_DIRNAME",
    "record_artifact",
    "record_source_file",
    "trace_job_dir",
    "trace_root",
    "write_ir_file",
]
