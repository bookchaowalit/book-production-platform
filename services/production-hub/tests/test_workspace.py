from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from production_hub import workspace
from production_hub.cli import main
from production_hub.contracts import ContractError
from production_hub.hub import ArtifactInput, Hub, Producer, Registered

GFX = Producer("graphics-v1a", "graphics.v1a", "b1")


def _register(hub: Hub, tmp_path: Path, name: str, content: bytes, *, kind: str = "graphic.png",
              project: str | None = "pod-1", qa: str = "accepted", privacy: str = "internal") -> Registered:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with hub.transaction() as conn:
        return hub.register(conn, ArtifactInput(path, kind, GFX, project_id=project, qa_status=qa,
                                                privacy_class=privacy))


def _project(hub: Hub, project_id: str = "pod-1") -> None:
    with hub.transaction() as conn:
        workspace.create_project(conn, project_id, "POD batch", client="Shop A")


def test_projects_collect_registered_artifacts(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    _project(hub)
    art = _register(hub, tmp_path, "a.png", b"a")
    with hub.transaction() as conn:
        members = workspace.project_artifacts(conn, "pod-1")
        listed = workspace.list_projects(conn)
    assert [item["artifact_id"] for item in members] == [art.artifact_id]
    assert members[0]["review_status"] == "draft"
    assert listed[0]["artifacts"] == 1
    with pytest.raises(workspace.WorkspaceError, match="already exists"), hub.transaction() as conn:
        workspace.create_project(conn, "pod-1", "again")
    with pytest.raises(workspace.WorkspaceError, match="lowercase"), hub.transaction() as conn:
        workspace.create_project(conn, "Bad Id", "x")


def test_review_transitions_and_history(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    _project(hub)
    art = _register(hub, tmp_path, "a.png", b"a")
    with hub.transaction() as conn:
        workspace.set_review(conn, "pod-1", art.artifact_id, "in_review")
        workspace.set_review(conn, "pod-1", art.artifact_id, "changes_requested", "logo too small")
        with pytest.raises(workspace.WorkspaceError, match="cannot move"):
            workspace.set_review(conn, "pod-1", art.artifact_id, "delivered")
        workspace.set_review(conn, "pod-1", art.artifact_id, "in_review")
        workspace.set_review(conn, "pod-1", art.artifact_id, "approved")
        history = workspace.review_history(conn, "pod-1", art.artifact_id)
    assert [event["to_status"] for event in history] == ["in_review", "changes_requested", "in_review", "approved"]
    assert history[1]["comment"] == "logo too small"


def test_qa_rejected_artifacts_cannot_be_approved(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    _project(hub)
    art = _register(hub, tmp_path, "bad.png", b"bad", qa="rejected")
    with pytest.raises(workspace.WorkspaceError, match="tool QA"), hub.transaction() as conn:
        workspace.set_review(conn, "pod-1", art.artifact_id, "approved")


def test_package_contains_only_approved_and_verifies(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    _project(hub)
    approved = _register(hub, tmp_path, "final.png", b"final")
    _register(hub, tmp_path, "draft.png", b"draft")
    personal = _register(hub, tmp_path, "face.png", b"face", privacy="personal")
    other = _register(hub, tmp_path, "other.png", b"other", project=None)
    with hub.transaction() as conn:
        workspace.set_review(conn, "pod-1", approved.artifact_id, "approved")
        workspace.set_review(conn, "pod-1", personal.artifact_id, "approved")
        result = workspace.build_package(hub, conn, "pod-1", tmp_path / "out")
    with zipfile.ZipFile(result.path) as archive:
        names = sorted(archive.namelist())
        manifest = json.loads(archive.read("delivery-manifest.json"))
        assert "โปรเจกต์" in archive.read("README.md").decode("utf-8")
        packed = archive.read("files/final.png")
    assert names == ["README.md", "delivery-manifest.json", "files/final.png"]
    assert manifest["files"][0]["artifact_id"] == "sha256:" + hashlib.sha256(packed).hexdigest()
    assert other.artifact_id not in json.dumps(manifest)
    with hub.db.connect() as conn:
        record = hub.db.get(conn, result.package.artifact_id)
    assert record is not None
    assert record["kind"] == "delivery.package"
    assert record["lineage"] == [approved.artifact_id]


def test_package_refuses_empty_or_corrupted_store(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    _project(hub)
    art = _register(hub, tmp_path, "final.png", b"final")
    with pytest.raises(workspace.WorkspaceError, match="no approved"), hub.transaction() as conn:
        workspace.build_package(hub, conn, "pod-1", tmp_path / "out")
    with hub.transaction() as conn:
        workspace.set_review(conn, "pod-1", art.artifact_id, "approved")
    hub.store.path_for(art.artifact_id).write_bytes(b"tampered")
    with pytest.raises(workspace.WorkspaceError, match="checksum"), hub.transaction() as conn:
        workspace.build_package(hub, conn, "pod-1", tmp_path / "out")


def _kit(tmp_path: Path, **overrides: object) -> Path:
    kit = {
        "schema_version": "brand-kit.v1", "brand_id": "shop-a", "name": "Shop A",
        "colors": {"primary": "#0A7CFF", "text": "#111111", "background": "#FFFFFF"},
        "fonts": {"heading": {"family": "Noto Sans Thai"}, "body": {"family": "Noto Sans Thai"}},
        "tone": {"voice": "friendly", "language": "th", "forbidden_words": ["ถูกที่สุด"]},
        **overrides,
    }
    path = tmp_path / "kit.json"
    path.write_text(json.dumps(kit, ensure_ascii=False), encoding="utf-8")
    return path


def test_brand_kit_register_and_load(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    logo = _register(hub, tmp_path, "logo.svg", b"<svg/>", kind="graphic.svg", project=None)
    path = _kit(tmp_path, logos=[{"role": "primary", "artifact_id": logo.artifact_id}])
    with hub.transaction() as conn:
        registered = workspace.register_brand_kit(hub, conn, path)
        loaded = workspace.load_brand_kit(hub, conn, "shop-a")
        record = hub.db.get(conn, registered.artifact_id)
    assert loaded["colors"]["primary"] == "#0A7CFF"
    assert record is not None and record["lineage"] == [logo.artifact_id]
    assert record["brand_id"] == "shop-a"


def test_brand_kit_validation(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with pytest.raises(ContractError), hub.transaction() as conn:
        workspace.register_brand_kit(hub, conn, _kit(tmp_path, colors={"primary": "blue"}))
    missing = _kit(tmp_path, logos=[{"role": "primary", "artifact_id": "sha256:" + "e" * 64}])
    with pytest.raises(workspace.WorkspaceError, match="unregistered"), hub.transaction() as conn:
        workspace.register_brand_kit(hub, conn, missing)


def test_cli_project_review_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "home")]
    assert main([*home, "project", "create", "pod-1", "--name", "POD", "--client", "Shop A"]) == 0
    art = _register(Hub(tmp_path / "home"), tmp_path, "final.png", b"final")
    assert main([*home, "review", "pod-1", art.artifact_id, "approved"]) == 0
    assert main([*home, "package", "pod-1", "--out", str(tmp_path / "out")]) == 0
    capsys.readouterr()
    assert main([*home, "project", "show", "pod-1"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert {item["review_status"] for item in shown} == {"approved", "draft"}
    assert main([*home, "review", "pod-1", art.artifact_id, "draft"]) == 2
