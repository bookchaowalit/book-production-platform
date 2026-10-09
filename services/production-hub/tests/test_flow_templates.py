from __future__ import annotations

from typing import Any

import pytest

from production_hub.flow_templates import FLOW_TEMPLATES, build_template, template_views
from production_hub.flows import FlowError, validate_flow

_SHA = "sha256:" + "a" * 64


def _inputs(**overrides: str) -> dict[str, str]:
    base = {
        "captions": _SHA,
        "stl": _SHA, "fcstd": _SHA, "qa_manifest": _SHA,
        "audio": _SHA,
    }
    base.update(overrides)
    return base


def _params(template_id: str, **overrides: str) -> dict[str, str]:
    required = {
        "promote-product": {"campaign_name": "คลิปใหม่", "headline": "พาดหัว", "title": "หน้าแลนดิง",
                            "cta_label": "ดูเพิ่ม", "features": "เร็ว:ทำงานไว"},
        "cad-render-and-spec": {"title": "สเปกชิ้นงาน", "customer_name": "ลูกค้า ก"},
        "audio-to-game": {"scene_id": "room-1"},
    }[template_id]
    return {**required, **overrides}


def test_every_template_builds_a_valid_flow() -> None:
    for template_id in FLOW_TEMPLATES:
        definition = build_template(template_id, "", _inputs(), _params(template_id))
        assert definition["flow_id"].startswith(f"{template_id}-")
        assert definition["project_id"] == template_id  # project falls back to the template id
        assert validate_flow(definition) == []


def test_views_expose_inputs_and_params_for_the_console() -> None:
    views = {view["id"]: view for view in template_views()}
    assert set(views) == {"promote-product", "cad-render-and-spec", "audio-to-game"}
    captions = {item["name"]: item for item in views["promote-product"]["inputs"]}
    assert captions["captions"]["kind"] == "captions.srt"
    channels = views["promote-product"]["params"]["channels"]
    assert channels["default"] == "facebook" and "facebook" in channels["choices"]
    assert views["audio-to-game"]["params"]["scene_id"]["required"] is True


def test_missing_required_param_or_input_is_rejected() -> None:
    with pytest.raises(FlowError, match="needs parameter"):
        build_template("promote-product", "", _inputs(), {})
    with pytest.raises(FlowError, match="needs input qa_manifest"):
        build_template("cad-render-and-spec", "", _inputs(qa_manifest=""), _params("cad-render-and-spec"))


def test_unknown_param_bad_choice_and_multi_channel_are_rejected() -> None:
    with pytest.raises(FlowError, match="does not take parameter"):
        build_template("audio-to-game", "", _inputs(), _params("audio-to-game", soundtrack="x"))
    with pytest.raises(FlowError, match="role must be one of"):
        build_template("audio-to-game", "", _inputs(), _params("audio-to-game", role="alarm"))
    with pytest.raises(FlowError, match="channels must be one of"):
        build_template("promote-product", "", _inputs(), _params("promote-product", channels="facebook,line"))
    with pytest.raises(FlowError, match="unknown flow template"):
        build_template("nope", "", {}, {})


def test_bad_project_id_is_rejected() -> None:
    with pytest.raises(FlowError, match="project_id must be lowercase"):
        build_template("audio-to-game", "Bad Project", _inputs(), _params("audio-to-game"))


def test_promote_flow_loads_and_fails_cleanly_on_unregistered_inputs(tmp_path: Any) -> None:
    """A built template flow loads, runs, and records the failure instead of crashing."""
    from production_hub.flows import load_flow, run_flow
    from production_hub.hub import Hub

    definition = build_template("promote-product", "promo", _inputs(), _params("promote-product"))
    hub = Hub(tmp_path / "home")
    flow_id = load_flow(hub, definition)
    status = run_flow(hub, flow_id)
    assert status.status == "failed"
    assert "not registered" in (status.steps[0].get("error") or "")
