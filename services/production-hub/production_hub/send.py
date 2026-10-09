"""Send registered artifacts to another tool (Production Hub H2).

A send resolves one handoff edge from ``registry/handoffs.yaml`` for the
artifact's kind and the target tool, materializes the artifacts from the store
into a work folder, lets the edge's ``SendAdapter`` write the target tool's
request, runs the tool's own CLI as a subprocess, and ingests the tool's output
manifest with the sent artifacts as lineage. The Hub never imports tool code.

Every send is recorded in ``hub.db`` (``sends`` table) with its status, so a
failed run leaves a trace but no half-registered outputs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hub import Hub
from .ingest import IngestError, IngestResult, ingest_manifest
from .registry import load_registries
from .workspace import assign

DEFAULT_TIMEOUT_SECONDS = 900

SENDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    send_id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL,
    to_tool TEXT NOT NULL,
    input_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    output_manifest_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
"""


class SendError(RuntimeError):
    """Raised when an artifact cannot be sent to the requested tool."""


@dataclass(frozen=True)
class Materialized:
    artifact_id: str
    kind: str
    path: Path
    record: Mapping[str, Any]


@dataclass(frozen=True)
class ToolRun:
    """The command an adapter wants run and where its output manifest will be."""

    argv: Sequence[str]
    cwd: Path
    manifest_path: Callable[[], Path]
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class SendContext:
    work_dir: Path
    artifacts: Sequence[Materialized]
    params: Mapping[str, str]
    send_id: str
    brand: Mapping[str, Any] | None = None


BuildRun = Callable[[SendContext], ToolRun]


@dataclass(frozen=True)
class OptionalParam:
    """A non-required send parameter the console can offer: its default and allowed values."""

    default: str = ""
    choices: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class SendAdapter:
    handoff_id: str
    build: BuildRun
    extra_kinds: frozenset[str] = frozenset()
    required_params: frozenset[str] = frozenset()
    optional_params: Mapping[str, OptionalParam] = field(default_factory=dict)


SEND_ADAPTERS: dict[str, SendAdapter] = {}


def register_adapter(adapter: SendAdapter) -> SendAdapter:
    SEND_ADAPTERS[adapter.handoff_id] = adapter
    return adapter


def choice_problem(adapter: SendAdapter, params: Mapping[str, str]) -> str | None:
    """Describe the first declared optional parameter set outside its choices, else None.

    Comma lists (``variants``, ``channels``) are checked item by item so the same
    declaration covers single-value and multi-value parameters.
    """
    for name in sorted(adapter.optional_params):
        value = params.get(name)
        choices = adapter.optional_params[name].choices
        if value is None or not choices:
            continue
        unknown = sorted({item.strip() for item in value.split(",") if item.strip()} - set(choices))
        if unknown:
            return f"{name} must be one of {', '.join(choices)}"
    return None


def preflight(
    hub: Hub,
    artifact_id: str,
    to_tool: str,
    *,
    extra_ids: Sequence[str] = (),
    params: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Cheap validation for a send before it is queued: registered accepted artifact,
    a send adapter for the edge, and complete parameters with valid choices."""
    params = dict(params or {})
    with hub.db.connect() as conn:
        primary = hub.db.get(conn, artifact_id)
        if primary is None:
            raise SendError(f"artifact is not registered: {artifact_id}")
        if primary["qa"]["status"] != "accepted":
            raise SendError(
                f"artifact QA status is {primary['qa']['status']}, only accepted artifacts can be sent"
            )
        extra_records = []
        for extra in extra_ids:
            extra_record = hub.db.get(conn, extra)
            if extra_record is None:
                raise SendError(f"artifact is not registered: {extra}")
            extra_records.append(extra_record)
    edge = resolve_handoff(primary["kind"], to_tool, primary["producer"]["tool_id"])
    adapter = SEND_ADAPTERS[edge["id"]]
    missing = sorted(adapter.required_params - params.keys())
    if missing:
        raise SendError(f"{edge['id']} needs parameter(s): {', '.join(missing)}")
    for item in extra_records:
        if item["kind"] not in adapter.extra_kinds:
            raise SendError(f"{edge['id']} does not accept an extra {item['kind']} input")
    problem = choice_problem(adapter, params)
    if problem:
        raise SendError(f"{edge['id']} {problem}")
    return primary


@dataclass(frozen=True)
class SendResult:
    send_id: str
    handoff_id: str
    work_dir: Path
    ingest: IngestResult | None
    dry_run: bool


def resolve_handoff(from_kind: str, to_tool: str, from_tool: str | None = None) -> dict[str, Any]:
    matches = [
        edge for edge in load_registries().handoffs
        if edge["from_kind"] == from_kind and edge["to_tool"] == to_tool
        # an authored source (producer "external", e.g. a product.v1 file in git) may feed any
        # edge that accepts its kind; tool outputs stay on edges declared for their tool
        and (from_tool in (None, "external") or edge["from_tool"] in (from_tool, "external"))
    ]
    with_adapter = [edge for edge in matches if edge["id"] in SEND_ADAPTERS]
    if not with_adapter:
        known = ", ".join(edge["id"] for edge in matches) or "none"
        raise SendError(f"no send adapter for {from_kind} -> {to_tool} (handoffs: {known})")
    return with_adapter[0]


def _materialize(hub: Hub, conn: Any, artifact_id: str, target_dir: Path) -> Materialized:
    record = hub.db.get(conn, artifact_id)
    if record is None:
        raise SendError(f"artifact is not registered: {artifact_id}")
    if record["qa"]["status"] != "accepted":
        raise SendError(f"artifact QA status is {record['qa']['status']}, only accepted artifacts can be sent")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{artifact_id[7:19]}-{record['filename']}"
    shutil.copyfile(hub.store.path_for(artifact_id), target)
    return Materialized(artifact_id, record["kind"], target, record)


def _join_project(conn: Any, project_id: str | None, registered: Sequence[Any]) -> None:
    """Make every send output a member of the project, including content that already
    existed in the store under another project (content-addressed ids are shared)."""
    if not project_id:
        return
    has_projects = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if has_projects and conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
        assign(conn, project_id, list(dict.fromkeys(item.artifact_id for item in registered)))


def _brand_kit(hub: Hub, conn: Any, params: dict[str, str], project_id: str | None) -> Mapping[str, Any] | None:
    """Brand kit from --param brand_id, else from the artifact's project, else none."""
    from .workspace import WorkspaceError, load_brand_kit

    brand_id = params.pop("brand_id", None)
    has_projects = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if brand_id is None and project_id and has_projects:
        row = conn.execute("SELECT brand_id FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        brand_id = row["brand_id"] if row else None
    if not brand_id:
        return None
    try:
        return load_brand_kit(hub, conn, brand_id)
    except WorkspaceError as exc:
        raise SendError(str(exc)) from exc


def _record(conn: Any, **values: Any) -> None:
    conn.executescript(SENDS_SCHEMA)
    conn.execute(
        """INSERT INTO sends (send_id, handoff_id, to_tool, input_ids, status, output_manifest_id, error, created_at)
           VALUES (:send_id, :handoff_id, :to_tool, :input_ids, :status, :output_manifest_id, :error, :created_at)
           ON CONFLICT(send_id) DO UPDATE SET status=excluded.status,
               output_manifest_id=excluded.output_manifest_id, error=excluded.error""",
        {"output_manifest_id": None, "error": None, **values},
    )
    conn.commit()


def send(
    hub: Hub,
    artifact_id: str,
    to_tool: str,
    *,
    extra_ids: Sequence[str] = (),
    params: Mapping[str, str] | None = None,
    project_id: str | None = None,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> SendResult:
    params = dict(params or {})
    send_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    work_dir = hub.home / "work" / send_id
    with hub.db.connect() as conn:
        primary = hub.db.get(conn, artifact_id)
        if primary is None:
            raise SendError(f"artifact is not registered: {artifact_id}")
        edge = resolve_handoff(primary["kind"], to_tool, primary["producer"]["tool_id"])
        adapter = SEND_ADAPTERS[edge["id"]]
        missing = sorted(adapter.required_params - params.keys())
        if missing:
            raise SendError(f"{edge['id']} needs parameter(s): {', '.join(missing)}")
        problem = choice_problem(adapter, params)
        if problem:
            raise SendError(f"{edge['id']} {problem}")
        artifacts = [_materialize(hub, conn, artifact_id, work_dir / "inputs")]
        for extra in extra_ids:
            item = _materialize(hub, conn, extra, work_dir / "inputs")
            if item.kind not in adapter.extra_kinds:
                raise SendError(f"{edge['id']} does not accept an extra {item.kind} input")
            artifacts.append(item)
        common = {
            "send_id": send_id, "handoff_id": edge["id"], "to_tool": to_tool,
            "input_ids": json.dumps([item.artifact_id for item in artifacts]),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        brand = _brand_kit(hub, conn, params, project_id or primary.get("project_id"))
        _record(conn, **common, status="prepared")

    run = adapter.build(SendContext(work_dir, artifacts, params, send_id, brand))
    (work_dir / "command.json").write_text(
        json.dumps({"argv": list(run.argv), "cwd": str(run.cwd)}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if dry_run:
        return SendResult(send_id, edge["id"], work_dir, None, True)

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **run.env}
    try:
        completed = runner(
            list(run.argv), cwd=run.cwd, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=run.timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        with hub.db.connect() as conn:
            _record(conn, **common, status="failed", error="timeout")
        raise SendError(f"{edge['id']} timed out after {run.timeout_seconds}s") from None
    (work_dir / "tool-stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (work_dir / "tool-stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        with hub.db.connect() as conn:
            _record(conn, **common, status="failed", error=f"exit {completed.returncode}")
        raise SendError(f"{edge['id']} tool run failed with exit {completed.returncode}; see {work_dir.name}/tool-stderr.log")

    try:
        manifest = run.manifest_path()
        target_project = project_id or primary.get("project_id")
        with hub.transaction() as conn:
            result = ingest_manifest(
                hub, conn, manifest, input_ids=[item.artifact_id for item in artifacts],
                project_id=target_project,
                privacy_class=primary["privacy_class"],
            )
            _join_project(conn, target_project, [result.manifest, *result.outputs])
    except (IngestError, FileNotFoundError) as exc:
        with hub.db.connect() as conn:
            _record(conn, **common, status="failed", error=f"ingest: {exc}")
        raise SendError(f"{edge['id']} output could not be ingested: {exc}") from exc
    with hub.db.connect() as conn:
        _record(conn, **common, status="completed", output_manifest_id=result.manifest.artifact_id)
    return SendResult(send_id, edge["id"], work_dir, result, False)
