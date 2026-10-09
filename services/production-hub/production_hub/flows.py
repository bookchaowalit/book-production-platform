"""Runnable handoff flows (``hub-flow.v1``, Production Hub E13).

A flow is an ordered list of steps:

- ``send``: run one implemented handoff; the primary input is a registered
  artifact id or the single output of an earlier step matching a kind (and
  optional attribute filter); ``with`` adds extra inputs the same way.
- ``gate``: stop until every output of an earlier step with the listed kinds
  is ``approved`` in the flow's project (H3 review). Nothing is approved
  automatically.

The whole flow is validated before the first step runs. Progress is kept in
``hub.db`` so ``resume`` continues after a gate or a fixed failure without
re-running completed steps. Flows never publish or send anything outside the
machine; delivery still goes through reviewed packages.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hub import Hub
from .registry import load_registries
from .send import SEND_ADAPTERS, SendError, choice_problem, send
from .workspace import create_project, list_projects, project_artifacts

FLOW_SCHEMA_VERSION = "hub-flow.v1"
_ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_STEPS = 30

FLOWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS flows (
    flow_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_steps (
    flow_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    send_id TEXT,
    output_ids TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (flow_id, step_id)
);
"""


class FlowError(ValueError):
    """Raised when a flow definition is invalid or cannot continue."""


@dataclass(frozen=True)
class FlowStatus:
    flow_id: str
    project_id: str
    status: str
    steps: list[dict[str, Any]]
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"flow_id": self.flow_id, "project_id": self.project_id, "status": self.status,
                "error": self.error, "steps": self.steps}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _selector_errors(selector: Any, label: str, earlier: set[str]) -> list[str]:
    if not isinstance(selector, dict):
        return [f"{label} must be an object"]
    if "artifact" in selector:
        return [] if isinstance(selector["artifact"], str) and _SHA.match(selector["artifact"]) else [
            f"{label}.artifact must be a sha256 artifact id"]
    errors = []
    if selector.get("from_step") not in earlier:
        errors.append(f"{label}.from_step must name an earlier step")
    if not isinstance(selector.get("kind"), str):
        errors.append(f"{label}.kind is required with from_step")
    if "filter" in selector and not isinstance(selector["filter"], dict):
        errors.append(f"{label}.filter must be an object")
    return errors


def validate_flow(definition: Mapping[str, Any]) -> list[str]:
    """Return every problem with the flow; an empty list means it can run."""
    errors: list[str] = []
    if definition.get("schema_version") != FLOW_SCHEMA_VERSION:
        errors.append(f"schema_version must be {FLOW_SCHEMA_VERSION}")
    for key in ("flow_id", "project_id"):
        if not isinstance(definition.get(key), str) or not _ID.match(definition[key]):
            errors.append(f"{key} must be lowercase letters, digits, - or _")
    steps = definition.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        return [*errors, f"steps must be a list of 1-{MAX_STEPS} steps"]
    edges = {edge["id"]: edge for edge in load_registries().handoffs}
    earlier: set[str] = set()
    for index, step in enumerate(steps):
        label = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{label} must be an object")
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not _ID.match(step_id) or step_id in earlier:
            errors.append(f"{label}.id must be a unique lowercase id")
        if ("send" in step) == ("gate" in step):
            errors.append(f"{label} needs exactly one of send or gate")
        elif "send" in step:
            spec = step["send"]
            handoff = spec.get("handoff") if isinstance(spec, dict) else None
            edge = edges.get(str(handoff))
            if edge is None or edge["status"] != "implemented" or handoff not in SEND_ADAPTERS:
                errors.append(f"{label}.send.handoff must be an implemented handoff with a send adapter")
            else:
                adapter = SEND_ADAPTERS[str(handoff)]
                params = spec.get("params", {})
                if not isinstance(params, dict) or not all(isinstance(v, str) for v in params.values()):
                    errors.append(f"{label}.send.params must map names to strings")
                else:
                    missing = sorted(adapter.required_params - params.keys() - {"brand_id"})
                    if missing:
                        errors.append(f"{label}.send.params is missing {', '.join(missing)}")
                    problem = choice_problem(adapter, params)
                    if problem:
                        errors.append(f"{label}.send.params {problem}")
                errors += _selector_errors(spec.get("input"), f"{label}.send.input", earlier)
                for position, extra in enumerate(spec.get("with", [])):
                    errors += _selector_errors(extra, f"{label}.send.with[{position}]", earlier)
        else:
            gate = step["gate"]
            if not isinstance(gate, dict) or gate.get("from_step") not in earlier:
                errors.append(f"{label}.gate.from_step must name an earlier step")
            elif not isinstance(gate.get("approve_kinds"), list) or not gate["approve_kinds"]:
                errors.append(f"{label}.gate.approve_kinds must list at least one kind")
        if isinstance(step_id, str):
            earlier.add(step_id)
    return errors


def _step_rows(conn: Any, flow_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM flow_steps WHERE flow_id = ?", (flow_id,)).fetchall()
    # identical content from one send is one artifact, so collapse repeated ids
    return {row["step_id"]: {**dict(row), "output_ids": list(dict.fromkeys(json.loads(row["output_ids"])))}
            for row in rows}


def _set_step(conn: Any, flow_id: str, step_id: str, status: str, *, send_id: str | None = None,
              outputs: Sequence[str] = (), error: str | None = None) -> None:
    conn.execute(
        """INSERT INTO flow_steps (flow_id, step_id, status, send_id, output_ids, error, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(flow_id, step_id) DO UPDATE SET status=excluded.status, send_id=excluded.send_id,
               output_ids=excluded.output_ids, error=excluded.error, updated_at=excluded.updated_at""",
        (flow_id, step_id, status, send_id, json.dumps(list(outputs)), error, _now()),
    )


def _set_flow(conn: Any, flow_id: str, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE flows SET status = ?, error = ?, updated_at = ? WHERE flow_id = ?",
                 (status, error, _now(), flow_id))
    conn.commit()


def _resolve(hub: Hub, conn: Any, selector: Mapping[str, Any], steps: Mapping[str, dict[str, Any]]) -> str:
    if "artifact" in selector:
        artifact_id = str(selector["artifact"])
        if hub.db.get(conn, artifact_id) is None:
            raise FlowError(f"artifact is not registered: {artifact_id}")
        return artifact_id
    # Identical content is one artifact, so the same id can appear more than once in a step's outputs.
    outputs = list(dict.fromkeys(steps.get(str(selector["from_step"]), {}).get("output_ids", [])))
    matches = []
    for artifact_id in outputs:
        record = hub.db.get(conn, artifact_id)
        if record is None or record["kind"] != selector["kind"]:
            continue
        attributes = record.get("attributes", {})
        if all(str(attributes.get(key)) == str(value) for key, value in dict(selector.get("filter", {})).items()):
            matches.append(artifact_id)
    if len(matches) != 1:
        raise FlowError(
            f"step {selector['from_step']} has {len(matches)} {selector['kind']} outputs matching the selector; need exactly one"
        )
    return str(matches[0])


def load_flow(hub: Hub, definition: Mapping[str, Any], *, conn: sqlite3.Connection | None = None) -> str:
    """Load a flow, optionally joining the owner's existing transaction."""
    errors = validate_flow(definition)
    if errors:
        raise FlowError("; ".join(errors))
    if conn is not None:
        return _load_flow(conn, definition)
    with hub.db.connect() as owned:
        flow_id = _load_flow(owned, definition)
        owned.commit()
        return flow_id


def _load_flow(conn: sqlite3.Connection, definition: Mapping[str, Any]) -> str:
    flow_id = str(definition["flow_id"])
    encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True)
    for statement in FLOWS_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    row = conn.execute("SELECT definition_json FROM flows WHERE flow_id = ?", (flow_id,)).fetchone()
    if row is not None and row["definition_json"] != encoded:
        raise FlowError(f"flow {flow_id} already exists with a different definition; use a new flow_id")
    if row is None:
        if definition["project_id"] not in {item["project_id"] for item in list_projects(conn)}:
            create_project(conn, str(definition["project_id"]), f"Flow {flow_id}")
        conn.execute(
            """INSERT INTO flows (flow_id, project_id, status, definition_json, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?, ?)""",
            (flow_id, definition["project_id"], encoded, _now(), _now()),
        )
    return flow_id


def run_flow(
    hub: Hub,
    flow_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FlowStatus:
    """Run or resume a loaded flow until it completes, waits at a gate, or a step fails."""
    with hub.db.connect() as conn:
        conn.executescript(FLOWS_SCHEMA)
        row = conn.execute("SELECT * FROM flows WHERE flow_id = ?", (flow_id,)).fetchone()
        if row is None:
            raise FlowError(f"unknown flow: {flow_id}")
        definition = json.loads(row["definition_json"])
        _set_flow(conn, flow_id, "running")
    edges = {edge["id"]: edge for edge in load_registries().handoffs}
    project_id = str(definition["project_id"])
    for step in definition["steps"]:
        with hub.db.connect() as conn:
            steps = _step_rows(conn, flow_id)
            if steps.get(step["id"], {}).get("status") == "completed":
                continue
            if "gate" in step:
                gate = step["gate"]
                kinds = set(gate["approve_kinds"])
                wanted = [artifact_id for artifact_id in steps.get(gate["from_step"], {}).get("output_ids", [])
                          if (hub.db.get(conn, artifact_id) or {}).get("kind") in kinds]
                reviews = {item["artifact_id"]: item["review_status"] for item in project_artifacts(conn, project_id)}
                conn.commit()
                pending = [artifact_id for artifact_id in wanted if reviews.get(artifact_id) != "approved"]
                if not wanted:
                    _set_step(conn, flow_id, step["id"], "failed", error="gate has no artifacts to approve")
                    _set_flow(conn, flow_id, "failed", f"{step['id']}: gate has no artifacts to approve")
                    break
                if pending:
                    _set_step(conn, flow_id, step["id"], "waiting_approval", outputs=pending)
                    _set_flow(conn, flow_id, "waiting_approval")
                    break
                _set_step(conn, flow_id, step["id"], "completed", outputs=wanted)
                conn.commit()
                continue
            spec = step["send"]
            try:
                primary = _resolve(hub, conn, spec["input"], steps)
                extras = [_resolve(hub, conn, extra, steps) for extra in spec.get("with", [])]
            except FlowError as exc:
                _set_step(conn, flow_id, step["id"], "failed", error=str(exc))
                _set_flow(conn, flow_id, "failed", f"{step['id']}: {exc}")
                break
            _set_step(conn, flow_id, step["id"], "running")
            conn.commit()
        edge = edges[spec["handoff"]]
        try:
            result = send(hub, primary, str(edge["to_tool"]), extra_ids=extras, params=dict(spec.get("params", {})),
                          project_id=project_id, runner=runner)
        except SendError as exc:
            with hub.db.connect() as conn:
                _set_step(conn, flow_id, step["id"], "failed", error=str(exc))
                _set_flow(conn, flow_id, "failed", f"{step['id']}: {exc}")
            break
        outputs = [item.artifact_id for item in result.ingest.outputs] if result.ingest else []
        with hub.db.connect() as conn:
            if result.handoff_id != spec["handoff"]:
                _set_step(conn, flow_id, step["id"], "failed", send_id=result.send_id, outputs=outputs,
                          error=f"ran {result.handoff_id} instead of {spec['handoff']}")
                _set_flow(conn, flow_id, "failed", f"{step['id']}: handoff mismatch")
                break
            _set_step(conn, flow_id, step["id"], "completed", send_id=result.send_id, outputs=outputs)
            conn.commit()
    else:
        with hub.db.connect() as conn:
            _set_flow(conn, flow_id, "completed")
    return flow_status(hub, flow_id)


def flow_status(hub: Hub, flow_id: str) -> FlowStatus:
    with hub.db.connect() as conn:
        conn.executescript(FLOWS_SCHEMA)
        row = conn.execute("SELECT * FROM flows WHERE flow_id = ?", (flow_id,)).fetchone()
        if row is None:
            raise FlowError(f"unknown flow: {flow_id}")
        definition = json.loads(row["definition_json"])
        steps = _step_rows(conn, flow_id)
    ordered = [
        {"step_id": step["id"], "type": "send" if "send" in step else "gate",
         **({"handoff": step["send"]["handoff"]} if "send" in step else {}),
         **{key: value for key, value in steps.get(step["id"], {"status": "pending"}).items() if key != "flow_id"}}
        for step in definition["steps"]
    ]
    return FlowStatus(flow_id, str(row["project_id"]), str(row["status"]), ordered, row["error"])


def list_flows(hub: Hub) -> list[dict[str, Any]]:
    """Every loaded flow, most recently updated first, with its step progress."""
    with hub.db.connect() as conn:
        conn.executescript(FLOWS_SCHEMA)
        rows = conn.execute("SELECT * FROM flows ORDER BY updated_at DESC, flow_id").fetchall()
        done = dict(conn.execute(
            "SELECT flow_id, COUNT(*) FROM flow_steps WHERE status = 'completed' GROUP BY flow_id").fetchall())
    return [
        {"flow_id": row["flow_id"], "project_id": row["project_id"], "status": row["status"], "error": row["error"],
         "steps": len(json.loads(row["definition_json"])["steps"]), "completed": done.get(row["flow_id"], 0),
         "updated_at": row["updated_at"]}
        for row in rows
    ]


def load_flow_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlowError(f"cannot read flow file: {exc}") from exc
    if not isinstance(data, dict):
        raise FlowError("a flow file must contain a JSON object")
    return data
