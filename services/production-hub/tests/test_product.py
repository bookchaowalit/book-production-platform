from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from production_hub import send as send_module
from production_hub import workspace
from production_hub.cli import main
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.product import ProductError, build_product, compile_flow, product_status, validate_product
from production_hub.send import SendAdapter, SendContext, ToolRun, resolve_handoff

_COUNTER = itertools.count()


def _fake_build(ctx: SendContext) -> ToolRun:
    out = ctx.work_dir / "out"
    return ToolRun(argv=["fake", str(out)], cwd=ctx.work_dir, manifest_path=lambda: out / "manifest-x.json")


def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    png = f"PNG card {next(_COUNTER)}".encode()
    (out / "card.png").write_bytes(png)
    (out / "manifest-x.json").write_text(json.dumps({
        "schema_version": "graphics-batch.v1", "batch_id": "b", "status": "accepted", "metadata": {},
        "items": [{"request_id": "card", "status": "accepted", "artifacts": [
            {"filename": "card.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}]}],
    }), encoding="utf-8")
    return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def fake_adapters() -> Iterator[None]:
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E6-copy-to-graphics", _fake_build, required_params=frozenset({"headline"})))
    yield
    send_module.SEND_ADAPTERS.clear()
    send_module.SEND_ADAPTERS.update(saved)


def _spec(tmp_path: Path, **overrides: Any) -> Path:
    folder = tmp_path / "catalog" / "products" / "demo"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pitch.txt").write_text("A short pitch.", encoding="utf-8")
    spec: dict[str, Any] = {
        "schema_version": "product.v1", "product_id": "demo", "name": "Demo product",
        "project": {"id": "demo-launch", "name": "Demo launch"},
        "sources": [{"id": "pitch", "path": "pitch.txt", "kind": "copy.text"}],
        "steps": [
            {"id": "card", "handoff": "E6-copy-to-graphics", "input": {"source": "pitch"}, "params": {"headline": "Hi"}},
            {"id": "review", "gate": {"from": "card", "approve_kinds": ["graphic.png"]}},
        ],
    }
    spec.update(overrides)
    path = folder / "product.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return path


def test_validation_reports_every_problem(tmp_path: Path) -> None:
    spec = {
        "schema_version": "product.v0", "product_id": "Bad", "name": "", "extra": 1,
        "sources": [{"id": "p", "path": "missing.txt", "kind": "nope"}, {"id": "p", "path": "../../../../x", "kind": "copy.text"}],
        "steps": [
            {"id": "a", "handoff": "E6-copy-to-graphics", "input": {"source": "zzz"}},
            {"id": "b", "gate": {"from": "later", "approve_kinds": []}},
            {"id": "c", "handoff": "E7b-graphic-to-web", "gate": {}, "input": {"from": "a"}},
            {"id": "a", "handoff": "E6-copy-to-graphics", "input": {"from": "a", "kind": "graphic.png"}, "typo": 1},
        ],
    }
    text = "\n".join(validate_product(spec, tmp_path, repo_root=tmp_path))
    for expected in ("schema_version", "product_id", "name is required", "unknown keys extra", "unknown artifact kind",
                     "must be a file inside the repository", "id must be unique", "unknown source 'zzz'",
                     "gate needs from", "exactly one of handoff or gate", "unknown keys typo"):
        assert expected in text


def test_compile_pins_the_flow_to_spec_and_sources() -> None:
    spec = {"product_id": "demo", "project": {"id": "demo-launch"}, "steps": [
        {"id": "card", "handoff": "E6-copy-to-graphics", "input": {"source": "pitch"}, "params": {"headline": 5}},
        {"id": "page", "handoff": "E7b-graphic-to-web", "input": {"from": "card", "kind": "graphic.png"},
         "with": [{"source": "pitch"}]},
        {"id": "review", "gate": {"from": "page", "approve_kinds": ["web.page"]}},
    ]}
    first = compile_flow(spec, {"pitch": "sha256:" + "a" * 64})
    assert first["flow_id"].startswith("demo-") and first["project_id"] == "demo-launch"
    assert first["steps"][0]["send"] == {"handoff": "E6-copy-to-graphics", "input": {"artifact": "sha256:" + "a" * 64},
                                         "params": {"headline": "5"}}
    assert first["steps"][1]["send"]["input"] == {"from_step": "card", "kind": "graphic.png"}
    assert first["steps"][2] == {"id": "review", "gate": {"from_step": "page", "approve_kinds": ["web.page"]}}
    assert compile_flow(spec, {"pitch": "sha256:" + "a" * 64})["flow_id"] == first["flow_id"]
    assert compile_flow(spec, {"pitch": "sha256:" + "b" * 64})["flow_id"] != first["flow_id"]


def test_authored_sources_may_use_any_edge_for_their_kind() -> None:
    assert resolve_handoff("copy.text", "graphics-v1a", "external")["id"] == "E6-copy-to-graphics"
    with pytest.raises(send_module.SendError):
        resolve_handoff("copy.text", "graphics-v1a", "documents-v1a")


def test_build_registers_sources_runs_gates_and_resumes(tmp_path: Path, fake_adapters: None,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("production_hub.product.WORKSPACE_ROOT", tmp_path)
    spec_path = _spec(tmp_path)
    hub = Hub(tmp_path / "home")
    dry = build_product(hub, spec_path, dry_run=True)
    assert dry.status is None
    first = build_product(hub, spec_path, runner=fake_run)
    assert first.flow_id == dry.flow_id
    assert first.status is not None and first.status.status == "waiting_approval"
    with hub.db.connect() as conn:
        source = hub.db.get(conn, first.sources["pitch"])
        members = {row["artifact_id"] for row in workspace.project_artifacts(conn, "demo-launch")}
    assert source is not None and source["producer"]["tool_id"] == "external"
    assert source["attributes"]["source_id"] == "pitch" and first.sources["pitch"] in members
    card = first.status.steps[0]["output_ids"][0]
    with hub.transaction() as conn:
        workspace.set_review(conn, "demo-launch", card, "approved")
    again = build_product(hub, spec_path, runner=fake_run)
    assert again.flow_id == first.flow_id and again.status is not None and again.status.status == "completed"
    with hub.db.connect() as conn:
        sends = conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
    assert sends == 1
    status = product_status(hub, spec_path)
    assert status.status is not None and status.status.status == "completed"
    (spec_path.parent / "pitch.txt").write_text("A changed pitch.", encoding="utf-8")
    assert product_status(hub, spec_path).status is None


def test_invalid_spec_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("production_hub.product.WORKSPACE_ROOT", tmp_path)
    spec_path = _spec(tmp_path, steps=[{"id": "x", "handoff": "E12a-model-to-game", "input": {"source": "pitch"}}])
    with pytest.raises(ProductError, match="implemented handoff"):
        build_product(Hub(tmp_path / "home"), spec_path, dry_run=True)
    home = ["--home", str(tmp_path / "home")]
    assert main([*home, "product", "build", str(spec_path), "--dry-run"]) == 2
    capsys.readouterr()
    good = _spec(tmp_path)
    assert main([*home, "product", "build", str(good), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["flow"]["steps"][0]["send"]["handoff"] == "E6-copy-to-graphics"


def _register(hub: Hub, tmp_path: Path, name: str, content: bytes, kind: str, **extra: Any) -> str:
    path = tmp_path / "files" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with hub.transaction() as conn:
        return hub.register(conn, ArtifactInput(path, kind, Producer("web-v1a", "v", "j"), project_id="site",
                                                **extra)).artifact_id


def test_package_ships_files_an_approved_page_links_to(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        workspace.create_project(conn, "site", "Site")
    hero = b"PNG hero bytes"
    hero_id = _register(hub, tmp_path, "mockup.png", hero, "graphic.png")
    css = b"body{}"
    manifest = {"artifacts": [
        {"filename": "page.css", "content_hash": hashlib.sha256(css).hexdigest()},
        {"filename": "page-hero.png", "content_hash": hashlib.sha256(hero).hexdigest()},
    ]}
    manifest_id = _register(hub, tmp_path, "manifest.json", json.dumps(manifest).encode(), "qa.manifest")
    html = b'<link href="page.css" rel="stylesheet"><img src="page-hero.png"><a href="https://x.test/">x</a>'
    html_id = _register(hub, tmp_path, "page.html", html, "web.page", attributes={"qa_manifest_id": manifest_id})
    css_id = _register(hub, tmp_path, "page.css", css, "web.page")
    with hub.transaction() as conn:
        for artifact_id in (html_id, css_id):
            workspace.set_review(conn, "site", artifact_id, "approved")
    with hub.transaction() as conn, pytest.raises(workspace.WorkspaceError, match="needs page-hero.png approved"):
        workspace.build_package(hub, conn, "site", tmp_path / "out")
    with hub.transaction() as conn:
        workspace.set_review(conn, "site", hero_id, "approved")
        result = workspace.build_package(hub, conn, "site", tmp_path / "out")
    names = zipfile.ZipFile(result.path).namelist()
    assert "files/page-hero.png" in names and "files/mockup.png" not in names


def test_package_rejects_links_the_web_job_did_not_produce(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        workspace.create_project(conn, "site", "Site")
    html_id = _register(hub, tmp_path, "page.html", b'<img src="ghost.png">', "web.page")
    with hub.transaction() as conn:
        workspace.set_review(conn, "site", html_id, "approved")
    with hub.transaction() as conn, pytest.raises(workspace.WorkspaceError, match="did not produce: ghost.png"):
        workspace.build_package(hub, conn, "site", tmp_path / "out")
