from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from production_hub import send as send_module
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.send import OptionalParam, SendAdapter, SendContext, SendError, ToolRun, preflight, send

VIZ = Producer("data-viz-v1a", "data-job.v1", "j1")


@pytest.fixture
def fake_adapter() -> Iterator[None]:
    """A test adapter on the planned E4 edge that 'renders' a graphics batch manifest."""

    def build(ctx: SendContext) -> ToolRun:
        out = ctx.work_dir / "out"
        request = {"chart": ctx.artifacts[0].path.name, "title": ctx.params["title"]}
        (ctx.work_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
        return ToolRun(argv=["fake-graphics", str(out)], cwd=ctx.work_dir, manifest_path=lambda: out / "manifest-x.json")

    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter(
        "E4-chart-to-graphics", build, required_params=frozenset({"title"}),
        optional_params={"variants": OptionalParam(default="square", choices=("square", "story"))},
    ))
    yield
    send_module.SEND_ADAPTERS.clear()
    send_module.SEND_ADAPTERS.update(saved)


def _fake_run(ok: bool = True) -> Any:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        png = b"\x89PNG chart post"
        (out / "post.png").write_bytes(png)
        (out / "manifest-x.json").write_text(json.dumps({
            "schema_version": "graphics-batch.v1", "batch_id": "gb9", "status": "accepted",
            "metadata": {"contract_version": "graphics.v1a"},
            "items": [{"request_id": "post", "status": "accepted", "artifacts": [
                {"filename": "post.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}
            ]}],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0 if ok else 3, "", "" if ok else "boom")

    return run


def _chart(hub: Hub, tmp_path: Path, qa: str = "accepted") -> str:
    path = tmp_path / "chart.svg"
    path.write_bytes(b"<svg>chart</svg>")
    with hub.transaction() as conn:
        return hub.register(conn, ArtifactInput(path, "chart.svg", VIZ, project_id="rep-1", qa_status=qa)).artifact_id


def _status(hub: Hub, send_id: str) -> str:
    with hub.db.connect() as conn:
        return str(conn.execute("SELECT status FROM sends WHERE send_id = ?", (send_id,)).fetchone()["status"])


def test_send_runs_tool_and_ingests_with_lineage(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    result = send(hub, chart_id, "graphics-v1a", params={"title": "ยอดขาย"}, runner=_fake_run())
    assert result.handoff_id == "E4-chart-to-graphics"
    assert result.ingest is not None
    post = result.ingest.outputs[0]
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, post.artifact_id) == [chart_id]
        record = hub.db.get(conn, post.artifact_id)
    assert record is not None and record["project_id"] == "rep-1"
    assert json.loads((result.work_dir / "request.json").read_text(encoding="utf-8"))["title"] == "ยอดขาย"
    assert _status(hub, result.send_id) == "completed"


def test_dry_run_writes_command_only(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    result = send(hub, _chart(hub, tmp_path), "graphics-v1a", params={"title": "x"}, dry_run=True,
                  runner=lambda *a, **k: pytest.fail("must not run"))
    assert (result.work_dir / "command.json").is_file()
    assert result.ingest is None


def test_send_failures_are_recorded(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    with pytest.raises(SendError, match="exit 3"):
        send(hub, chart_id, "graphics-v1a", params={"title": "x"}, runner=_fake_run(ok=False))
    with hub.db.connect() as conn:
        rows = conn.execute("SELECT status, error FROM sends").fetchall()
    assert [(row["status"], row["error"]) for row in rows] == [("failed", "exit 3")]


def test_send_guards(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    with pytest.raises(SendError, match="needs parameter"):
        send(hub, chart_id, "graphics-v1a", runner=_fake_run())
    with pytest.raises(SendError, match="no send adapter"):
        send(hub, chart_id, "web-v1a", params={"title": "x"}, runner=_fake_run())
    with pytest.raises(SendError, match="not registered"):
        send(hub, "sha256:" + "d" * 64, "graphics-v1a", runner=_fake_run())
    rejected = Hub(tmp_path / "home2")
    bad = _chart(rejected, tmp_path, qa="rejected")
    with pytest.raises(SendError, match="only accepted"):
        send(rejected, bad, "graphics-v1a", params={"title": "x"}, runner=_fake_run())


def test_send_checks_declared_optional_choices(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    with pytest.raises(SendError, match="variants must be one of square, story"):
        send(hub, chart_id, "graphics-v1a", params={"title": "x", "variants": "bogus"}, runner=_fake_run())
    result = send(hub, chart_id, "graphics-v1a", params={"title": "x", "variants": "square,story"}, runner=_fake_run())
    assert result.handoff_id == "E4-chart-to-graphics"


def test_preflight_validates_before_queueing(tmp_path: Path, fake_adapter: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    primary = preflight(hub, chart_id, "graphics-v1a", params={"title": "x"})
    assert primary["artifact_id"] == chart_id
    with pytest.raises(SendError, match="needs parameter"):
        preflight(hub, chart_id, "graphics-v1a")
    with pytest.raises(SendError, match="variants must be one of"):
        preflight(hub, chart_id, "graphics-v1a", params={"title": "x", "variants": "nope"})
    with pytest.raises(SendError, match="no send adapter"):
        preflight(hub, chart_id, "web-v1a", params={"title": "x"})
    with pytest.raises(SendError, match="not registered"):
        preflight(hub, "sha256:" + "d" * 64, "graphics-v1a")
