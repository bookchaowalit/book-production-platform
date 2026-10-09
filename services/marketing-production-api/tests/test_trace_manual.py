from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from marketing_ir import CampaignIR
from production_trace import read_history, sha256_json
from production_trace.cli import main as trace_cli

from marketing_production import jobs
from marketing_production.cli import main as marketing_cli
from marketing_production.jobs import run_campaign_job
from marketing_production.manual import FRAME_SIZES, build_marketing_guide, export_name
from marketing_production.proposal_trace import trace_proposal

CANARY = "CANARY-mkt-trace-41d2"


def _payload(asset_root: Path, **extra: Any) -> dict[str, Any]:
    return {
        "campaign_id": "launch-01",
        "name": f"Launch {CANARY}",
        "objective": "awareness",
        "channels": ["facebook", "instagram", "linkedin", "email", "line", "web"],
        "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "th"}]},
        "assets": [
            {"asset_id": "fb-copy", "kind": "copy", "channel": "facebook", "uri": "inline:fb", "text": f"โปรใหม่ #sale {CANARY}"},
            {"asset_id": "fb-image", "kind": "image", "channel": "facebook", "uri": "creative.png"},
            {"asset_id": "ig-copy", "kind": "copy", "channel": "instagram", "uri": "inline:ig", "text": "ลองเลย #sale @brand"},
            {"asset_id": "li-copy", "kind": "copy", "channel": "linkedin", "uri": "inline:li", "text": "New product for teams"},
            {"asset_id": "mail", "kind": "email", "channel": "email", "uri": "inline:mail", "text": "=Hello members"},
            {"asset_id": "line-copy", "kind": "copy", "channel": "line", "uri": "inline:line", "text": "ข่าวดีสำหรับสมาชิก"},
            {"asset_id": "web-page", "kind": "landing_page", "channel": "web", "uri": "inline:web"},
        ],
        "asset_root": str(asset_root),
        **extra,
    }


def _job_dir(out: Path, key: str) -> Path:
    return out / "jobs" / hashlib.sha256(key.encode()).hexdigest()[:16]


def _trace(out: Path) -> dict[str, Any]:
    entry = read_history(out)[-1]
    return dict(json.loads((out / entry["trace"]).read_text(encoding="utf-8")))


@pytest.fixture
def asset_root(tmp_path: Path) -> Path:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    return tmp_path


def test_accepted_job_writes_trace_ir_manual_history_and_frozen_package(asset_root: Path) -> None:
    out = asset_root / "out"
    result = run_campaign_job(_payload(asset_root), out, "key-1")
    assert result.status == "accepted" and result.package is not None
    job_dir = _job_dir(out, "key-1")
    trace = json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "accepted" and trace["tool_id"] == "marketing-v1a"
    assert [step["operation"] for step in trace["steps"]] == [
        "parse_request", "validate_request", "compile_campaign", "channel_rules", "build_tracked_links",
        "hash_sources", "write_package", "run_package_qa", "write_manifest",
    ]
    ir = json.loads((job_dir / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"] == {"schema": "campaign-ir.v1", "sha256": sha256_json(ir), "file": "ir.json"}
    assert (job_dir / result.package.filename).read_bytes() == (out / result.package.filename).read_bytes()
    assert trace["artifacts"][0]["sha256"] == result.package.content_hash
    assert [item["name"] for item in trace["inputs"]] == ["request", "fb-image"]
    assert trace["manual_guide"]["file"] == "MANUAL-STEPS.md"
    history = read_history(out)
    assert len(history) == 1 and history[0]["status"] == "accepted" and history[0]["job_id"] == trace["job_id"]


def test_trace_and_history_hold_no_copy_text(asset_root: Path) -> None:
    out = asset_root / "out"
    run_campaign_job(_payload(asset_root), out, "key-1")
    bad = _payload(asset_root)
    bad["assets"][0]["text"] = f"{CANARY} " + "#x " * 31
    bad["channels"] = ["facebook", "instagram"]
    bad["assets"] = [{**bad["assets"][2], "text": f"{CANARY} " + " ".join(f"#t{i}" for i in range(31))}, bad["assets"][0]]
    assert run_campaign_job(bad, out, "key-2").error_code == "hashtag_limit_exceeded"
    for path in [*out.glob("jobs/*/trace.json"), out / "history.jsonl"]:
        text = path.read_text(encoding="utf-8")
        assert CANARY not in text and "โปรใหม่" not in text and "Hello members" not in text, path


def test_verify_passes_fresh_and_fails_after_tampering(asset_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = asset_root / "out"
    result = run_campaign_job(_payload(asset_root), out, "key-1")
    job_id = read_history(out)[-1]["job_id"]
    assert trace_cli(["verify", "--root", str(out), job_id]) == 0
    assert result.package is not None
    frozen = _job_dir(out, "key-1") / result.package.filename
    frozen.write_text(frozen.read_text(encoding="utf-8").replace("launch-01", "launch-99"), encoding="utf-8")
    assert trace_cli(["verify", "--root", str(out), job_id]) == 1
    assert '"ok": false' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(channels=["facebook", "facebook"]), "duplicate_channel"),
        (lambda p: p.update(objective="sell-everything"), None),
        (lambda p: p["assets"].__setitem__(1, {**p["assets"][1], "uri": "missing.png"}), "asset_missing"),
    ],
)
def test_rejected_jobs_are_traced_as_failed(asset_root: Path, mutate: Any, code: str | None) -> None:
    payload = _payload(asset_root)
    mutate(payload)
    result = run_campaign_job(payload, asset_root / "out", "bad-1")
    assert result.status == "rejected"
    if code is not None:
        assert result.error_code == code
    trace = _trace(asset_root / "out")
    assert trace["status"] == "failed" and trace["steps"][-1]["operation"] == "failed"
    assert read_history(asset_root / "out")[-1]["status"] == "failed"
    assert not list((asset_root / "out").glob("manifest-*.json"))
    assert len(trace["steps"][-1]["note"]) <= 120 and " " not in trace["steps"][-1]["note"]


def test_unexpected_error_is_traced_and_still_raised(asset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("disk")

    monkeypatch.setattr(jobs, "run_package_qa", boom)
    with pytest.raises(RuntimeError, match="disk"):
        run_campaign_job(_payload(asset_root), asset_root / "out", "boom-1")
    trace = _trace(asset_root / "out")
    assert trace["status"] == "failed" and trace["steps"][-1]["note"] == "RuntimeError"


def test_replay_of_v1_manifest_still_works_after_recipe_upgrade(asset_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "RECIPE_VERSION", "campaign-package-v1")
    first = run_campaign_job(_payload(asset_root), asset_root / "out", "upgrade-1")
    monkeypatch.undo()
    replay = run_campaign_job(_payload(asset_root), asset_root / "out", "upgrade-1")
    assert replay.replayed is True and replay.package == first.package
    assert len(read_history(asset_root / "out")) == 1


def test_v1_request_package_has_no_v2_keys(asset_root: Path) -> None:
    result = run_campaign_job(_payload(asset_root), asset_root / "out", "plain-1")
    assert result.package is not None
    package = json.loads((asset_root / "out" / result.package.filename).read_text(encoding="utf-8"))
    assert "utm" not in package["campaign"] and all("variant" not in asset for asset in package["campaign"]["assets"])
    assert "links" not in package and "experiments" not in package
    assert "checks" not in package  # no hashtag findings in this request


def test_guide_has_every_channel_copy_slot_and_review_only_ending(asset_root: Path) -> None:
    payload = _payload(asset_root, utm={"landing_url": "https://example.com/launch"})
    payload["assets"].append({"asset_id": "ig-copy-b", "kind": "copy", "channel": "instagram", "uri": "inline:igb", "text": "เวอร์ชัน B", "variant": "B"})
    payload["assets"][2]["variant"] = "A"
    result = run_campaign_job(payload, asset_root / "out", "guide-1")
    assert result.status == "accepted" and result.package is not None
    package = json.loads((asset_root / "out" / result.package.filename).read_text(encoding="utf-8"))
    ir = CampaignIR.model_validate(package["campaign"])
    guide = build_marketing_guide(ir, links=package["links"], experiments=package["experiments"], checks=package.get("checks", []))
    values = [step.value for step in guide.steps]
    for asset in ir.assets:
        if asset.text:
            assert asset.text in values, asset.asset_id
    for link in package["links"]:
        assert link["url"] in values
    sections = {step.section for step in guide.steps}
    for channel in ir.channels:
        channel_steps = [step for step in guide.steps if step.section.startswith(f"{channel}:")]
        assert channel_steps[-1].action.startswith("บันทึกเป็นแบบร่าง"), channel
        assert not any("Publish" == step.tool or "Send" == step.tool for step in channel_steps)
    assert "Canva: เตรียมภาพ/วิดีโอขนาดตามช่องทาง" in sections
    image = next(asset for asset in ir.assets if asset.kind == "image")
    width, height, _ = FRAME_SIZES["facebook"]
    assert f"Width {width} px × Height {height} px" in values and export_name(image) in values
    assert any(step.tool.startswith("Post details ▸ A/B test") and step.value == "A=ig-copy, B=ig-copy-b" for step in guide.steps)
    assert guide.program == "Mailchimp + Meta Business Suite + LINE Official Account Manager + LinkedIn + WordPress (Block Editor) + Canva"
    manual = (_job_dir(asset_root / "out", "guide-1") / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "review-only" in manual and package["links"][0]["url"] in manual


def test_cli_build_from_proposal_records_ai(asset_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proposal = {"status": "complete", "request": _payload(asset_root), "adapter": "provider", "model_calls": 1, "provided": ["copy"], "inferred": []}
    source = asset_root / "proposal.json"
    source.write_text(json.dumps(proposal), encoding="utf-8")
    assert marketing_cli(["build", "--input", str(source), "--output", str(asset_root / "out"), "--idempotency-key", "ai-1"]) == 0
    trace = _trace(asset_root / "out")
    assert trace["ai"]["used"] is True and trace["ai"]["adapter"] == "provider"
    source.write_text(json.dumps({**proposal, "status": "needs_input", "request": None}), encoding="utf-8")
    assert marketing_cli(["build", "--input", str(source), "--output", str(asset_root / "out"), "--idempotency-key", "ai-2"]) == 2
    assert "not complete" in capsys.readouterr().err


def test_trace_proposal_is_metadata_only(tmp_path: Path) -> None:
    result = {"status": "complete", "request": {"campaign_id": "c"}, "adapter": "provider", "adapter_version": "brief-adapter.v1",
              "model_calls": 1, "provided": ["copy"], "inferred": [], "missing": [], "issues": []}
    output = tmp_path / "proposal.json"
    output.write_text(json.dumps(result), encoding="utf-8")
    trace = trace_proposal(result, output, "p-1", envelope={"brief": f"brief {CANARY}"})
    assert trace is not None and trace["status"] == "proposed"
    assert trace_cli(["verify", "--root", str(tmp_path), trace["job_id"]]) == 0
    assert CANARY not in (tmp_path / "history.jsonl").read_text(encoding="utf-8")
