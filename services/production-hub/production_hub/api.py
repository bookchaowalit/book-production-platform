"""Local HTTP API for the Production Console (Production Hub H4).

Binds to 127.0.0.1 only. Read endpoints return artifact metadata, lineage,
projects, and registry data; ``/content`` streams one stored file. Mutations
(POST) require the ``X-Hub-Client: console`` header, which a cross-site form
cannot send, and, when ``PRODUCTION_HUB_API_TOKEN`` is set, a matching
``Authorization: Bearer`` token.

``POST /api/v1/send`` and ``POST /api/v1/flows[.../resume]`` queue a background
job and return ``job_id`` immediately; ``GET /api/v1/jobs/{id}`` reports the
status, a short log, and the final result. ``dry_run`` sends still answer
synchronously.

Run from ``services/production-hub``::

    python -m production_hub.api --port 3460
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import adapters as _adapters  # noqa: F401 - registers send adapters
from . import workspace
from .contracts import ContractError
from .flow_templates import build_template, template_views
from .flows import FlowError, flow_status, list_flows, load_flow
from .formats import detect_runtimes, load_format_registry, route_report, summary
from .hub import Hub
from .jobs import FlowBusy, IdempotencyConflict, JobManager
from .registry import load_registries
from .send import SEND_ADAPTERS, SendError, preflight, send

SERVICE = "production-hub-api"
DEFAULT_PORT = 3460
MAX_BODY = 64 * 1024
_ID = r"sha256:[0-9a-f]{64}"
_PROJECT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_JOB = r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}"
TOKEN_ENV = "PRODUCTION_HUB_API_TOKEN"


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _available_sends(kind: str, tool_id: str) -> list[dict[str, Any]]:
    edges = []
    for edge in load_registries().handoffs:
        if edge["from_kind"] != kind or edge["from_tool"] not in (tool_id, "external"):
            continue
        adapter = SEND_ADAPTERS.get(edge["id"])
        edges.append({
            "handoff_id": edge["id"], "to_tool": edge["to_tool"], "status": edge["status"],
            "available": adapter is not None,
            "required_params": sorted(adapter.required_params) if adapter else [],
            "optional_params": {
                name: {"default": param.default, "choices": list(param.choices),
                       "description": param.description}
                for name, param in sorted(adapter.optional_params.items())
            } if adapter else {},
            "extra_kinds": sorted(adapter.extra_kinds) if adapter else [],
        })
    return edges


class HubApi:
    def __init__(self, hub: Hub, runner: Any = subprocess.run) -> None:
        self.hub = hub
        self.jobs = JobManager(hub, runner=runner)
        self._runtimes: dict[str, dict[str, Any]] | None = None

    def close(self) -> None:
        """Stop the background job pool (the API server is shutting down)."""
        self.jobs.close()

    def runtimes(self, refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Runtime detection takes seconds (module lookups), so keep one result per process."""
        if self._runtimes is None or refresh:
            self._runtimes = detect_runtimes(load_format_registry())
        return self._runtimes

    def get(self, path: str, query: dict[str, list[str]]) -> tuple[Any, str | None]:
        first = {key: values[0] for key, values in query.items() if values}
        if path == "/api/v1/health":
            return {"service": SERVICE, "status": "ok"}, None
        if path == "/api/v1/registry":
            registries = load_registries()
            return {
                "kinds": registries.kinds, "tools": registries.tools,
                "handoffs": [dict(edge, adapter=edge["id"] in SEND_ADAPTERS) for edge in registries.handoffs],
            }, None
        if path == "/api/v1/formats":
            registry = load_format_registry()
            return {
                "summary": summary(registry), "levels": registry.levels, "domains": registry.domains,
                "formats": registry.formats, "effects": registry.effects, "converters": registry.converters,
            }, None
        if path == "/api/v1/runtimes":
            return {"runtimes": self.runtimes(refresh=first.get("refresh") == "1")}, None
        if path == "/api/v1/path":
            source, target = first.get("from", "").strip(), first.get("to", "").strip()
            if not source or not target:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_request", "ต้องระบุ from และ to")
            try:
                limit = max(1, min(int(first.get("limit", "5")), 10))
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_request", "limit ต้องเป็นตัวเลข") from exc
            try:
                return route_report(
                    load_format_registry(), source, target, implemented_only=first.get("implemented_only") == "1",
                    limit=limit, installed=self.runtimes(),
                ), None
            except KeyError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, "unknown_format", f"ไม่รู้จักรูปแบบไฟล์ {exc.args[0]!r}") from exc
        if match := re.fullmatch(rf"/api/v1/jobs/({_JOB})", path):
            job = self.jobs.status(match.group(1))
            if job is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบ job นี้")
            return job, None
        if path == "/api/v1/flow-templates":
            return {"items": template_views()}, None
        with self.hub.db.connect() as conn:
            if path == "/api/v1/artifacts":
                limit = min(int(first.get("limit", "200")), 500)
                rows = self.hub.db.list(
                    conn, kind=first.get("kind"), tool_id=first.get("tool"), project_id=first.get("project"),
                    limit=limit,
                )
                return {"items": [dict(row) for row in rows]}, None
            if match := re.fullmatch(rf"/api/v1/artifacts/({_ID})(/lineage|/content)?", path):
                artifact_id, suffix = match.groups()
                record = self.hub.db.get(conn, artifact_id)
                if record is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบไฟล์นี้ในคลัง")
                if suffix == "/lineage":
                    return self.hub.lineage_tree(conn, artifact_id), None
                if suffix == "/content":
                    return self.hub.store.path_for(artifact_id), str(record["media_type"])
                record["used_by"] = self.hub.db.outputs(conn, artifact_id)
                record["sends"] = _available_sends(record["kind"], record["producer"]["tool_id"])
                return record, None
            if path == "/api/v1/projects":
                return {"items": workspace.list_projects(conn)}, None
            if match := re.fullmatch(r"/api/v1/flows/([a-z0-9]+(?:[-_][a-z0-9]+)*)", path):
                try:
                    status = flow_status(self.hub, match.group(1))
                except FlowError as exc:
                    raise ApiError(HTTPStatus.NOT_FOUND, "not_found", str(exc)) from exc
                return status.to_dict(), None
            if path == "/api/v1/flows":
                return {"items": list_flows(self.hub)}, None
            if match := re.fullmatch(rf"/api/v1/projects/({_PROJECT})", path):
                project_id = match.group(1)
                conn.commit()
                projects = {item["project_id"]: item for item in workspace.list_projects(conn)}
                if project_id not in projects:
                    raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบโปรเจกต์นี้")
                items = workspace.project_artifacts(conn, project_id)
                conn.commit()
                return {"project": projects[project_id], "artifacts": items}, None
        raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบ endpoint นี้")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            return self._post(path, body)
        except IdempotencyConflict as exc:
            raise ApiError(HTTPStatus.CONFLICT, "idempotency_conflict", str(exc)) from exc

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        if match := re.fullmatch(rf"/api/v1/jobs/({_JOB})/cancel", path):
            job_id = match.group(1)
            if self.jobs.status(job_id) is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบ job นี้")
            if not self.jobs.cancel(job_id):
                raise ApiError(HTTPStatus.CONFLICT, "job_not_cancellable", "job ไม่อยู่ในคิว external ที่ยกเลิกได้")
            return self.jobs.status(job_id)
        template = re.fullmatch(r"/api/v1/flow-templates/([a-z0-9]+(?:-[a-z0-9]+)*)", path)
        if template is not None:
            if self.jobs.mode == "inline" and "idempotency_key" in body:
                raise ApiError(HTTPStatus.BAD_REQUEST, "unsupported_idempotency",
                               "template idempotency_key ต้องใช้ external job mode")
            try:
                if self.jobs.mode == "external":
                    request = {"template_id": template.group(1),
                               "body": {key: value for key, value in body.items() if key != "idempotency_key"}}
                    return self.jobs.submit_template(
                        request,
                        lambda: build_template(template.group(1), str(body.get("project_id", "")),
                                               dict(body.get("inputs", {})), dict(body.get("params", {}))),
                        idempotency_key=body.get("idempotency_key"),
                    )
                definition = build_template(
                    template.group(1), str(body.get("project_id", "")),
                    dict(body.get("inputs", {})), dict(body.get("params", {})),
                )
                flow_id = load_flow(self.hub, definition)
            except FlowError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "flow_error", str(exc)) from exc
            try:
                job = self.jobs.submit_flow(flow_id, idempotency_key=body.get("idempotency_key"))
            except FlowBusy as exc:
                raise ApiError(HTTPStatus.CONFLICT, "flow_busy", str(exc)) from exc
            return {"job_id": job["job_id"], "flow_id": flow_id, "status": job["status"]}
        resume = re.fullmatch(r"/api/v1/flows/([a-z0-9]+(?:[-_][a-z0-9]+)*)/resume", path)
        if path == "/api/v1/flows" or resume is not None:
            try:
                flow_id = resume.group(1) if resume is not None else load_flow(self.hub, body)
                if resume is not None:
                    flow_status(self.hub, flow_id)  # unknown flow stays a 422, as before queuing existed
            except FlowError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "flow_error", str(exc)) from exc
            try:
                job = self.jobs.submit_flow(flow_id, idempotency_key=body.get("idempotency_key"))
            except FlowBusy as exc:
                raise ApiError(HTTPStatus.CONFLICT, "flow_busy", str(exc)) from exc
            return {"job_id": job["job_id"], "flow_id": flow_id, "status": job["status"]}
        if path == "/api/v1/send":
            artifact_id, to_tool = str(body.get("artifact_id", "")), str(body.get("to", ""))
            params = {str(k): str(v) for k, v in dict(body.get("params", {})).items()}
            extra_ids = [str(item) for item in body.get("with", [])]
            try:
                if body.get("dry_run", False):
                    result = send(self.hub, artifact_id, to_tool, extra_ids=extra_ids, params=params,
                                  dry_run=True)
                    return {
                        "send_id": result.send_id, "handoff_id": result.handoff_id, "dry_run": True,
                        "outputs": [],
                    }
                preflight(self.hub, artifact_id, to_tool, extra_ids=extra_ids, params=params)
            except SendError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "send_error", str(exc)) from exc
            job = self.jobs.submit_send({
                "artifact_id": artifact_id, "to": to_tool, "with": extra_ids, "params": params,
                "project_id": body.get("project_id"),
            }, idempotency_key=body.get("idempotency_key"))
            return {"job_id": job["job_id"], "kind": job["kind"], "status": job["status"]}
        try:
            with self.hub.transaction() as conn:
                if path == "/api/v1/projects":
                    workspace.create_project(
                        conn, str(body.get("project_id", "")), str(body.get("name") or body.get("project_id", "")),
                        body.get("client") or None, body.get("brand_id") or None,
                    )
                    return {"project_id": body["project_id"], "created": True}
                if match := re.fullmatch(rf"/api/v1/projects/({_PROJECT})/(review|assign|package)", path):
                    project_id, action = match.groups()
                    if action == "review":
                        previous = workspace.set_review(
                            conn, project_id, str(body.get("artifact_id", "")), str(body.get("status", "")),
                            body.get("comment") or None,
                        )
                        return {"from": previous, "to": body["status"]}
                    if action == "assign":
                        workspace.assign(conn, project_id, [str(item) for item in body.get("artifact_ids", [])])
                        return {"assigned": len(body.get("artifact_ids", []))}
                    out = self.hub.home / "packages" / project_id
                    package = workspace.build_package(self.hub, conn, project_id, out)
                    return {"artifact_id": package.package.artifact_id, "path": str(package.path),
                            "files": package.files}
        except workspace.WorkspaceError as exc:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "workspace_error", str(exc)) from exc
        except ContractError as exc:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "contract_violation", str(exc)) from exc
        raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "ไม่พบ endpoint นี้")


def make_handler(api: HubApi, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVICE

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: HTTPStatus, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _fail(self, error: ApiError) -> None:
            self._send_json(error.status, {"error": {"code": error.code, "message": error.message}})

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            try:
                payload, media_type = api.get(url.path, parse_qs(url.query))
            except ApiError as exc:
                self._fail(exc)
                return
            except (ValueError, KeyError) as exc:
                self._fail(ApiError(HTTPStatus.BAD_REQUEST, "bad_request", str(exc)))
                return
            if media_type is None:
                self._send_json(HTTPStatus.OK, payload)
                return
            path: Path = payload
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.headers.get("X-Hub-Client") != "console":
                self._fail(ApiError(HTTPStatus.FORBIDDEN, "forbidden", "missing X-Hub-Client header"))
                return
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self._fail(ApiError(HTTPStatus.UNAUTHORIZED, "unauthorized", "invalid token"))
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._fail(ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "request body too large"))
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise TypeError("body must be a JSON object")
                payload = api.post(urlsplit(self.path).path, body)
            except ApiError as exc:
                self._fail(exc)
                return
            except (ValueError, KeyError, TypeError) as exc:
                self._fail(ApiError(HTTPStatus.BAD_REQUEST, "bad_request", str(exc)))
                return
            status = HTTPStatus.OK
            path = urlsplit(self.path).path
            if (api.jobs.mode == "external" and isinstance(payload, dict) and "job_id" in payload
                    and (path == "/api/v1/send" or path == "/api/v1/flows"
                         or path.startswith("/api/v1/flow-templates/") or path.endswith("/resume"))):
                status = HTTPStatus.ACCEPTED
            self._send_json(status, payload)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--home", type=Path, default=None)
    args = parser.parse_args(argv)
    api = HubApi(Hub(args.home))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(api, os.environ.get(TOKEN_ENV)))
    print(f"{SERVICE} listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        api.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
