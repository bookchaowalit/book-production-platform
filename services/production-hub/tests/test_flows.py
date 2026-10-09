from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from production_hub import send as send_module
from production_hub import workspace
from production_hub.api import HubApi
from production_hub.cli import main
from production_hub.flows import FlowError, flow_status, list_flows, load_flow, run_flow, validate_flow
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.send import OptionalParam, SendAdapter, SendContext, ToolRun

_COUNTER = itertools.count()


def _fake_build(ctx: SendContext) -> ToolRun:
    out = ctx.work_dir / "out"
    return ToolRun(argv=["fake", str(out)], cwd=ctx.work_dir, manifest_path=lambda: out / "manifest-x.json")


@pytest.fixture
def fake_adapters() -> Iterator[None]:
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter("E4-chart-to-graphics", _fake_build, required_params=frozenset({"title"})))
    send_module.register_adapter(SendAdapter("E3-graphic-to-video", _fake_build))
    yield
    send_module.SEND_ADAPTERS.clear()
    send_module.SEND_ADAPTERS.update(saved)


def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    png = f"\x89PNG {next(_COUNTER)}".encode()
    (out / "card.png").write_bytes(png)
    (out / "manifest-x.json").write_text(json.dumps({
        "schema_version": "graphics-batch.v1", "batch_id": "b", "status": "accepted", "metadata": {},
        "items": [{"request_id": "card", "status": "accepted", "artifacts": [
            {"filename": "card.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}]}],
    }), encoding="utf-8")
    return subprocess.CompletedProcess(argv, 0, "", "")


def _chart(hub: Hub, tmp_path: Path) -> str:
    path = tmp_path / "chart.svg"
    path.write_bytes(b"<svg>flow</svg>")
    with hub.transaction() as conn:
        return hub.register(conn, ArtifactInput(path, "chart.svg", Producer("data-viz-v1a", "v", "j"))).artifact_id


def _flow(chart_id: str, **overrides: Any) -> dict[str, Any]:
    flow = {
        "schema_version": "hub-flow.v1", "flow_id": "launch-1", "project_id": "launch",
        "steps": [
            {"id": "card", "send": {"handoff": "E4-chart-to-graphics", "input": {"artifact": chart_id},
                                    "params": {"title": "t"}}},
            {"id": "review", "gate": {"from_step": "card", "approve_kinds": ["graphic.png"]}},
            {"id": "clip", "send": {"handoff": "E3-graphic-to-video",
                                    "input": {"from_step": "card", "kind": "graphic.png"}}},
        ],
    }
    flow.update(overrides)
    return flow


def test_validation_reports_every_problem(fake_adapters: None) -> None:
    sha = "sha256:" + "a" * 64
    errors = validate_flow({
        "schema_version": "x", "flow_id": "Bad Id", "project_id": "p",
        "steps": [
            {"id": "a", "send": {"handoff": "E12a-model-to-game", "input": {"artifact": sha}}},
            {"id": "b", "send": {"handoff": "E4-chart-to-graphics", "input": {"from_step": "later", "kind": "x"}}},
            {"id": "c", "gate": {"from_step": "a", "approve_kinds": []}},
            {"id": "a", "gate": {"from_step": "zzz", "approve_kinds": ["x"]}},
        ],
    })
    text = "\n".join(errors)
    for expected in ("schema_version", "flow_id", "implemented handoff", "missing title",
                     "earlier step", "approve_kinds", "unique lowercase id"):
        assert expected in text


def test_validation_checks_optional_param_choices(fake_adapters: None) -> None:
    saved = dict(send_module.SEND_ADAPTERS)
    send_module.register_adapter(SendAdapter(
        "E4-chart-to-graphics", _fake_build, required_params=frozenset({"title"}),
        optional_params={"variants": OptionalParam(default="square", choices=("square", "story"))}))
    try:
        errors = validate_flow({
            "schema_version": "hub-flow.v1", "flow_id": "launch-1", "project_id": "p",
            "steps": [{"id": "a", "send": {"handoff": "E4-chart-to-graphics",
                                           "input": {"artifact": "sha256:" + "a" * 64},
                                           "params": {"title": "t", "variants": "bogus"}}}],
        })
    finally:
        send_module.SEND_ADAPTERS.clear()
        send_module.SEND_ADAPTERS.update(saved)
    assert "steps[0].send.params variants must be one of square, story" in "\n".join(errors)


def test_flow_waits_at_gate_then_resumes_after_approval(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    flow_id = load_flow(hub, _flow(chart_id))
    first = run_flow(hub, flow_id, runner=fake_run)
    assert first.status == "waiting_approval"
    assert [step["status"] for step in first.steps] == ["completed", "waiting_approval", "pending"]
    card_id = first.steps[0]["output_ids"][0]
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, card_id) == [chart_id]

    again = run_flow(hub, flow_id, runner=fake_run)
    assert again.status == "waiting_approval"

    with hub.transaction() as conn:
        workspace.set_review(conn, "launch", card_id, "approved")
    done = run_flow(hub, flow_id, runner=fake_run)
    assert done.status == "completed"
    assert [step["status"] for step in done.steps] == ["completed", "completed", "completed"]
    clip_id = done.steps[2]["output_ids"][0]
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, clip_id) == [card_id]
        sends = conn.execute("SELECT COUNT(*) FROM sends WHERE handoff_id = 'E4-chart-to-graphics'").fetchone()[0]
    assert sends == 1


def test_failed_step_stops_the_flow(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    flow_id = load_flow(hub, _flow(chart_id))

    def failing(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 3, "", "boom")

    status = run_flow(hub, flow_id, runner=failing)
    assert status.status == "failed"
    assert status.steps[0]["status"] == "failed" and "exit 3" in status.steps[0]["error"]
    assert status.steps[1]["status"] == "pending"


def test_redefining_a_flow_id_is_rejected(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    load_flow(hub, _flow(chart_id))
    assert load_flow(hub, _flow(chart_id)) == "launch-1"
    with pytest.raises(FlowError, match="different definition"):
        load_flow(hub, _flow(chart_id, project_id="other"))
    with pytest.raises(FlowError, match="unknown flow"):
        flow_status(hub, "missing")


def test_cli_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str], fake_adapters: None,
                  monkeypatch: pytest.MonkeyPatch) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    import functools

    from production_hub import cli

    monkeypatch.setattr(cli, "run_flow", functools.partial(run_flow, runner=fake_run))
    flow_file = tmp_path / "flow.json"
    flow_file.write_text(json.dumps(_flow(chart_id)), encoding="utf-8")
    home = ["--home", str(tmp_path / "home")]
    assert main([*home, "flow", "run", str(flow_file)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "waiting_approval"
    assert main([*home, "flow", "status", "launch-1"]) == 0
    assert json.loads(capsys.readouterr().out)["steps"][1]["status"] == "waiting_approval"
    assert main([*home, "flow", "status", "nope"]) == 2


def test_duplicate_outputs_of_identical_content_resolve_once(tmp_path: Path) -> None:
    from production_hub.flows import _resolve

    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    with hub.db.connect() as conn:
        steps = {"a": {"output_ids": [chart_id, chart_id]}}
        assert _resolve(hub, conn, {"from_step": "a", "kind": "chart.svg"}, steps) == chart_id


def test_list_flows_and_api_views(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    assert list_flows(hub) == []
    flow_id = load_flow(hub, _flow(chart_id))
    run_flow(hub, flow_id, runner=fake_run)
    [row] = list_flows(hub)
    assert row["flow_id"] == "launch-1" and row["project_id"] == "launch"
    assert (row["status"], row["steps"], row["completed"]) == ("waiting_approval", 3, 1)
    api = HubApi(hub)
    listed, _ = api.get("/api/v1/flows", {})
    assert listed["items"][0]["flow_id"] == "launch-1"
    detail, _ = api.get("/api/v1/flows/launch-1", {})
    assert detail["project_id"] == "launch"
    assert [step.get("handoff") for step in detail["steps"]] == ["E4-chart-to-graphics", None, "E3-graphic-to-video"]


def test_status_lists_each_output_once(tmp_path: Path, fake_adapters: None) -> None:
    from production_hub.flows import _set_step

    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)
    flow_id = load_flow(hub, _flow(chart_id))
    with hub.db.connect() as conn:
        _set_step(conn, flow_id, "card", "completed", outputs=[chart_id, chart_id])
        conn.commit()
    assert flow_status(hub, flow_id).steps[0]["output_ids"] == [chart_id]


def test_outputs_shared_with_another_project_join_the_flow_project(tmp_path: Path, fake_adapters: None) -> None:
    hub = Hub(tmp_path / "home")
    chart_id = _chart(hub, tmp_path)

    def same_png(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        png = b"PNG identical"
        (out / "card.png").write_bytes(png)
        (out / "manifest-x.json").write_text(json.dumps({
            "schema_version": "graphics-batch.v1", "batch_id": "b", "status": "accepted", "metadata": {},
            "items": [{"request_id": "card", "status": "accepted", "artifacts": [
                {"filename": "card.png", "media_type": "image/png", "content_hash": hashlib.sha256(png).hexdigest()}]}],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    first = run_flow(hub, load_flow(hub, _flow(chart_id)), runner=same_png)
    second = run_flow(hub, load_flow(hub, _flow(chart_id, flow_id="launch-2", project_id="launch-b")), runner=same_png)
    card_id = second.steps[0]["output_ids"][0]
    assert card_id == first.steps[0]["output_ids"][0]
    with hub.transaction() as conn:
        members = {row["artifact_id"] for row in workspace.project_artifacts(conn, "launch-b")}
        assert card_id in members
        workspace.set_review(conn, "launch-b", card_id, "approved")
    assert run_flow(hub, "launch-2", runner=same_png).status == "completed"
