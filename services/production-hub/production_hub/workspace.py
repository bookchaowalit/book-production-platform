"""Projects, review states, brand kits, and delivery packages (Production Hub H3).

- A project groups artifacts for one client job; artifacts join a project when
  registered with ``project_id`` or via ``assign``.
- Review states per artifact and project: ``draft -> in_review -> approved ->
  delivered`` (``changes_requested`` returns to draft). Every change is kept in
  ``review_events``.
- A brand kit is a ``brand-kit.v1`` JSON registered as a ``brand.kit`` artifact;
  its font and logo references must be registered artifacts.
- A delivery package is a zip holding only the project's ``approved``
  artifacts, a ``delivery-manifest.json``, and a Thai README. Store objects are
  re-hashed before packing and the zip is re-read and checked before it is
  registered as ``delivery.package``. Nothing is sent anywhere automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import ContractError
from .hub import ArtifactInput, Hub, Producer, Registered
from .paths import SCHEMAS_DIR

REVIEW_STATES = ("draft", "in_review", "changes_requested", "approved", "delivered")
_TRANSITIONS = {
    "draft": {"in_review", "approved"},
    "in_review": {"approved", "changes_requested"},
    "changes_requested": {"draft", "in_review"},
    "approved": {"delivered", "changes_requested"},
    "delivered": set(),
}
_PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HUB_VERSION = "production-hub.h3"

WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    client TEXT,
    brand_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_artifacts (
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    review_status TEXT NOT NULL DEFAULT 'draft',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, artifact_id)
);
CREATE TABLE IF NOT EXISTS review_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);
"""


class WorkspaceError(ValueError):
    """Raised for invalid project, review, brand, or package operations."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ensure(conn: sqlite3.Connection) -> None:
    # executescript commits an existing caller transaction, even for IF NOT EXISTS.
    for statement in WORKSPACE_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def create_project(
    conn: sqlite3.Connection, project_id: str, name: str, client: str | None = None, brand_id: str | None = None
) -> None:
    _ensure(conn)
    if not _PROJECT_ID.match(project_id):
        raise WorkspaceError("project id must be lowercase letters, digits, and hyphens")
    if conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
        raise WorkspaceError(f"project already exists: {project_id}")
    conn.execute(
        "INSERT INTO projects (project_id, name, client, brand_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, name, client, brand_id, _now()),
    )


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    _ensure(conn)
    rows = conn.execute(
        """SELECT p.project_id, p.name, p.client, p.brand_id, p.status, p.created_at,
                  COUNT(pa.artifact_id) AS artifacts,
                  SUM(pa.review_status = 'approved') AS approved
           FROM projects p LEFT JOIN project_artifacts pa ON pa.project_id = p.project_id
           GROUP BY p.project_id ORDER BY p.created_at DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def _project(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    _ensure(conn)
    row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        raise WorkspaceError(f"unknown project: {project_id}")
    project: sqlite3.Row = row
    return project


def sync_project_members(conn: sqlite3.Connection, project_id: str) -> int:
    """Add artifacts registered with this project_id that are not members yet."""
    _project(conn, project_id)
    cursor = conn.execute(
        """INSERT OR IGNORE INTO project_artifacts (project_id, artifact_id, updated_at)
           SELECT project_id, artifact_id, ? FROM artifacts WHERE project_id = ?""",
        (_now(), project_id),
    )
    return cursor.rowcount


def assign(conn: sqlite3.Connection, project_id: str, artifact_ids: Sequence[str]) -> None:
    _project(conn, project_id)
    for artifact_id in artifact_ids:
        if conn.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone() is None:
            raise WorkspaceError(f"artifact is not registered: {artifact_id}")
        conn.execute(
            "INSERT OR IGNORE INTO project_artifacts (project_id, artifact_id, updated_at) VALUES (?, ?, ?)",
            (project_id, artifact_id, _now()),
        )


def set_review(
    conn: sqlite3.Connection, project_id: str, artifact_id: str, to_status: str, comment: str | None = None
) -> str:
    if to_status not in REVIEW_STATES:
        raise WorkspaceError(f"review status must be one of {', '.join(REVIEW_STATES)}")
    sync_project_members(conn, project_id)
    row = conn.execute(
        "SELECT review_status FROM project_artifacts WHERE project_id = ? AND artifact_id = ?",
        (project_id, artifact_id),
    ).fetchone()
    if row is None:
        raise WorkspaceError(f"artifact is not part of project {project_id}")
    current = str(row["review_status"])
    if to_status not in _TRANSITIONS[current]:
        raise WorkspaceError(f"cannot move review from {current} to {to_status}")
    if to_status == "approved":
        qa = conn.execute("SELECT qa_status FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if qa["qa_status"] != "accepted":
            raise WorkspaceError("only artifacts whose tool QA accepted them can be approved")
    conn.execute(
        "UPDATE project_artifacts SET review_status = ?, updated_at = ? WHERE project_id = ? AND artifact_id = ?",
        (to_status, _now(), project_id, artifact_id),
    )
    conn.execute(
        """INSERT INTO review_events (project_id, artifact_id, from_status, to_status, comment, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (project_id, artifact_id, current, to_status, comment, _now()),
    )
    return current


def project_artifacts(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    sync_project_members(conn, project_id)
    rows = conn.execute(
        """SELECT a.artifact_id, a.kind, a.filename, a.byte_size, a.tool_id, a.qa_status, a.privacy_class,
                  pa.review_status, pa.updated_at
           FROM project_artifacts pa JOIN artifacts a ON a.artifact_id = pa.artifact_id
           WHERE pa.project_id = ? ORDER BY a.kind, a.filename""",
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def review_history(conn: sqlite3.Connection, project_id: str, artifact_id: str) -> list[dict[str, Any]]:
    _ensure(conn)
    rows = conn.execute(
        """SELECT from_status, to_status, comment, created_at FROM review_events
           WHERE project_id = ? AND artifact_id = ? ORDER BY event_id""",
        (project_id, artifact_id),
    ).fetchall()
    return [dict(row) for row in rows]


@cache
def _brand_validator() -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / "brand-kit.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def register_brand_kit(hub: Hub, conn: sqlite3.Connection, path: Path) -> Registered:
    try:
        kit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read brand kit: {exc}") from exc
    errors = [error.message for error in _brand_validator().iter_errors(kit)]
    if errors:
        raise ContractError([f"brand kit: {message}" for message in errors])
    refs = [font["artifact_id"] for font in kit["fonts"].values() if "artifact_id" in font]
    refs += [logo["artifact_id"] for logo in kit.get("logos", [])]
    for ref in refs:
        if hub.db.get(conn, ref) is None:
            raise WorkspaceError(f"brand kit references an unregistered artifact: {ref}")
    return hub.register(conn, ArtifactInput(
        path, "brand.kit", Producer("production-hub", HUB_VERSION, f"brand:{kit['brand_id']}"),
        lineage=refs, brand_id=kit["brand_id"], attributes={"brand_name": kit["name"]},
    ))


def load_brand_kit(hub: Hub, conn: sqlite3.Connection, brand_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT artifact_id FROM artifacts WHERE kind = 'brand.kit'
           AND json_extract(record_json, '$.brand_id') = ? ORDER BY created_at DESC LIMIT 1""",
        (brand_id,),
    ).fetchone()
    if row is None:
        raise WorkspaceError(f"no brand kit registered for {brand_id}")
    kit: dict[str, Any] = json.loads(hub.store.path_for(row["artifact_id"]).read_text(encoding="utf-8"))
    return kit


@dataclass(frozen=True)
class PackageResult:
    package: Registered
    path: Path
    files: int


_README_TH = """# แพ็กเกจส่งมอบ: {name}

- โปรเจกต์: `{project_id}`{client_line}
- สร้างเมื่อ: {created_at}
- จำนวนไฟล์: {count}

ไฟล์ทั้งหมดในโฟลเดอร์ `files/` ผ่านการตรวจคุณภาพของเครื่องมือและได้รับการอนุมัติแล้ว
รายการไฟล์พร้อม SHA-256 อยู่ใน `delivery-manifest.json` ใช้ตรวจว่าไฟล์ไม่เสียหายระหว่างส่งได้
"""


_LOCAL_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)["']""", re.IGNORECASE)


def _page_references(hub: Hub, conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, str]:
    """Local files an HTML page links to, mapped to artifact ids through its web job manifest."""
    html = hub.store.path_for(record["artifact_id"]).read_text(encoding="utf-8", errors="replace")
    refs = {ref for ref in _LOCAL_REF.findall(html) if "://" not in ref and not ref.startswith(("mailto:", "/"))}
    if not refs:
        return {}
    manifest_id = (record.get("attributes") or {}).get("qa_manifest_id")
    listed: dict[str, str] = {}
    if manifest_id and hub.db.get(conn, manifest_id) is not None:
        try:
            manifest = json.loads(hub.store.path_for(manifest_id).read_text(encoding="utf-8"))
            listed = {str(item["filename"]): f"sha256:{item['content_hash']}"
                      for item in manifest.get("artifacts", []) if item.get("filename") and item.get("content_hash")}
        except (OSError, ValueError, KeyError, TypeError):
            listed = {}
    missing = sorted(ref for ref in refs if ref not in listed)
    if missing:
        raise WorkspaceError(f"{record['filename']} links to files its web job did not produce: {', '.join(missing)}")
    return {ref: listed[ref] for ref in sorted(refs)}


def build_package(
    hub: Hub, conn: sqlite3.Connection, project_id: str, out_dir: Path, *, include_personal: bool = False
) -> PackageResult:
    project = _project(conn, project_id)
    members = [
        item for item in project_artifacts(conn, project_id)
        if item["review_status"] == "approved" and item["kind"] != "delivery.package"
    ]
    if not include_personal:
        members = [item for item in members if item["privacy_class"] != "personal"]
    if not members:
        raise WorkspaceError("no approved artifacts to package")
    created_at = _now()
    names: set[str] = set()
    entries: list[dict[str, Any]] = []
    approved = {item["artifact_id"] for item in members}
    # an approved page must ship with every local file it links to, under the name it links to
    aliases: dict[str, str] = {}
    for item in members:
        record = hub.db.get(conn, item["artifact_id"]) or {}
        if item["kind"] != "web.page" or record.get("media_type") != "text/html":
            continue
        for ref, artifact_id in _page_references(hub, conn, record).items():
            if artifact_id not in approved:
                raise WorkspaceError(f"{item['filename']} needs {ref} approved before it can be delivered")
            aliases.setdefault(artifact_id, ref)
    for item in members:
        if not hub.store.verify(item["artifact_id"]):
            raise WorkspaceError(f"store object failed its checksum: {item['artifact_id']}")
        name = aliases.get(item["artifact_id"], item["filename"])
        if name in names:
            name = f"{item['artifact_id'][7:15]}-{name}"
        names.add(name)
        record = hub.db.get(conn, item["artifact_id"]) or {}
        entries.append({
            "path": f"files/{name}", "artifact_id": item["artifact_id"], "kind": item["kind"],
            "byte_size": item["byte_size"], "producer": record.get("producer"),
        })
    manifest = {
        "schema_version": "delivery-manifest.v1", "project_id": project_id, "project_name": project["name"],
        "client": project["client"], "created_at": created_at, "files": entries,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = created_at.replace(":", "").replace("-", "")[:15]
    zip_path = out_dir / f"{project_id}-delivery-{stamp}.zip"
    readme = _README_TH.format(
        name=project["name"], project_id=project_id, created_at=created_at, count=len(entries),
        client_line=f"\n- ลูกค้า: {project['client']}" if project["client"] else "",
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.write(hub.store.path_for(entry["artifact_id"]), entry["path"])
        archive.writestr("delivery-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("README.md", readme)
    with zipfile.ZipFile(zip_path) as archive:
        for entry in entries:
            digest = "sha256:" + hashlib.sha256(archive.read(entry["path"])).hexdigest()
            if digest != entry["artifact_id"]:
                zip_path.unlink()
                raise WorkspaceError(f"packed file failed its checksum: {entry['path']}")
    package = hub.register(conn, ArtifactInput(
        zip_path, "delivery.package", Producer("production-hub", HUB_VERSION, f"package:{project_id}:{stamp}"),
        lineage=[entry["artifact_id"] for entry in entries], project_id=project_id,
        privacy_class="client-confidential", attributes={"files": len(entries)},
    ))
    return PackageResult(package, zip_path, len(entries))
