"""Bounded subprocess invocation of the existing stage CLIs.

The stage CLIs are invoked exactly as their owners document them (module
entry points with explicit ``PYTHONPATH``); no stage code is imported here.
Every call uses typed argv, ``shell=False``, a bounded timeout, and captured
output. Artifact integrity of each stage result is verified against the
stage manifest before the next stage starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Protocol

from .contracts import StageJobManifest
from .paths import PRODUCTION_TRACE_ROOT

__all__ = ["StageCommand", "StageInvoker", "SubprocessInvoker", "read_json_file", "verify_stage_result"]


class StageFailure(RuntimeError):
    """Raised when a stage CLI fails, times out, or emits unreadable output."""

    def __init__(self, stage: str, code: str, detail: str) -> None:
        self.stage = stage
        self.code = code
        self.detail = detail
        super().__init__(f"{stage}: {code}: {detail}")


class StageCommand:
    """One bounded stage invocation."""

    def __init__(self, stage: str, argv: list[str], cwd: Path, env: dict[str, str], timeout_seconds: float) -> None:
        self.stage = stage
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.timeout_seconds = timeout_seconds


class CompletedStage:
    """Outcome of one stage invocation (subset of subprocess.CompletedProcess)."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class StageInvoker(Protocol):
    """Callable that executes a stage command (injectable for tests)."""

    def __call__(self, command: StageCommand) -> CompletedStage: ...


class SubprocessInvoker:
    """Real bounded subprocess invoker used in production runs."""

    def __call__(self, command: StageCommand) -> CompletedStage:
        try:
            completed = subprocess.run(
                command.argv,
                cwd=command.cwd,
                env=command.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=False,
                timeout=command.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StageFailure(command.stage, "stage_timeout", f"timed out after {command.timeout_seconds:g}s") from exc
        except OSError as exc:
            raise StageFailure(command.stage, "stage_spawn_failed", str(exc)) from exc
        return CompletedStage(completed.returncode, completed.stdout, completed.stderr)


def build_stage_env(entries: list[str]) -> dict[str, str]:
    """Environment for a stage CLI with the required paths first on PYTHONPATH."""

    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [*entries, str(PRODUCTION_TRACE_ROOT), *(part for part in existing.split(os.pathsep) if part)]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def parse_stage_json(stage: str, stdout: str) -> dict[str, Any]:
    """Parse the JSON job result printed by a stage CLI."""

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise StageFailure(stage, "stage_output_unreadable", f"stdout is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageFailure(stage, "stage_output_unreadable", "stdout JSON is not an object")
    return payload


def read_json_file(stage: str, path: Path) -> dict[str, Any]:
    """Read and parse a JSON file produced by a stage."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StageFailure(stage, "artifact_unreadable", f"cannot read {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StageFailure(stage, "artifact_unreadable", f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageFailure(stage, "artifact_unreadable", f"{path.name} is not a JSON object")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_stage_result(stage: str, job_root: Path, payload: dict[str, Any]) -> StageJobManifest:
    """Validate a stage manifest and the integrity of every listed artifact.

    ``job_root`` is the directory the manifest artifact paths resolve against
    (the job directory for data/spreadsheet, the document job directory for
    documents). Raises :class:`StageFailure` with the stable code
    ``artifact_integrity_failed`` on any mismatch.
    """

    try:
        manifest = StageJobManifest.model_validate(payload)
    except Exception as exc:
        raise StageFailure(stage, "contract_violation", f"stage manifest does not match the consumed subset: {exc}") from exc
    if manifest.status != "accepted":
        code = manifest.error_code or "stage_rejected"
        raise StageFailure(stage, code, f"stage reported status {manifest.status!r}")
    artifacts = manifest.all_artifacts()
    if not artifacts:
        raise StageFailure(stage, "contract_violation", "stage manifest lists no artifacts")
    for artifact in artifacts:
        artifact_path = job_root / artifact.filename
        if not artifact_path.is_file():
            raise StageFailure(stage, "artifact_integrity_failed", f"missing artifact: {artifact.filename}")
        actual_size = artifact_path.stat().st_size
        if actual_size != artifact.byte_size:
            raise StageFailure(
                stage,
                "artifact_integrity_failed",
                f"size mismatch for {artifact.filename}: {actual_size} != {artifact.byte_size}",
            )
        if _sha256_file(artifact_path) != artifact.content_hash:
            raise StageFailure(stage, "artifact_integrity_failed", f"hash mismatch for {artifact.filename}")
    return manifest
