from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from production_hub.cli import main
from production_hub.formats import (
    FormatRegistry,
    check_format_registry,
    detect_runtime,
    find_paths,
    load_format_registry,
    resolve_node,
    route_report,
)
from production_hub.registry import load_catalog_tool_ids, load_registries

HUB = load_registries()
CATALOG = load_catalog_tool_ids()
REGISTRY = load_format_registry()


def _mutable() -> FormatRegistry:
    return replace(
        REGISTRY,
        domains=copy.deepcopy(REGISTRY.domains),
        formats=copy.deepcopy(REGISTRY.formats),
        effects=copy.deepcopy(REGISTRY.effects),
        converters=copy.deepcopy(REGISTRY.converters),
        runtimes=copy.deepcopy(REGISTRY.runtimes),
    )


def _converter(registry: FormatRegistry, converter_id: str) -> dict[str, object]:
    return next(item for item in registry.converters if item["id"] == converter_id)


def _errors(registry: FormatRegistry) -> str:
    return "\n".join(check_format_registry(registry, HUB, CATALOG))


def test_repository_format_registry_is_consistent() -> None:
    assert check_format_registry(REGISTRY, HUB, CATALOG) == []


def test_every_hub_kind_and_handoff_is_in_the_graph() -> None:
    kinds = {kind for entry in REGISTRY.formats.values() for kind in entry.get("kinds") or []}
    assert set(HUB.kinds) <= kinds
    linked = {item["handoff"] for item in REGISTRY.converters if item.get("handoff")}
    assert linked == {edge["id"] for edge in HUB.handoffs}


def test_effects_need_l6_and_human_approval() -> None:
    registry = _mutable()
    _converter(registry, "print-3d").pop("approval")
    assert "effects need level L6 and approval: human" in _errors(registry)
    registry = _mutable()
    _converter(registry, "slice")["to"] = ["gcode", "effect.printed-part"]
    text = _errors(registry)
    assert "effects need level L6" in text and "outputs effects only" in text


def test_restricted_domains_cap_the_level() -> None:
    registry = _mutable()
    _converter(registry, "hdl-sim")["level"] = "L4"
    assert "exceeds domain hardware-design max_level L2" in _errors(registry)
    registry = _mutable()
    registry.converters.append({"id": "fly", "from": ["mission-plan"], "to": ["effect.printed-part"], "tool": "x",
                                "level": "L6", "status": "planned", "approval": "human"})
    registry.effects["effect.printed-part"]["domain"] = "vehicles"
    assert "exceeds domain vehicles max_level L2" in _errors(registry)


def test_implemented_converters_must_match_real_tools_and_handoffs() -> None:
    registry = _mutable()
    _converter(registry, "cad-part")["to"] = ["pdf"]
    assert "cad-v1a does not produce any kind stored as pdf" in _errors(registry)
    registry = _mutable()
    _converter(registry, "pandoc")["status"] = "implemented"
    assert "must use a catalog tool" in _errors(registry)
    registry = _mutable()
    _converter(registry, "ocr-table")["status"] = "implemented"
    assert "disagrees with handoff E8-ocr-table-to-data" in _errors(registry)
    registry = _mutable()
    _converter(registry, "audio-to-game").pop("handoff")
    assert "handoff E12b-audio-to-game: no converter links it" in _errors(registry)


def test_formats_must_cover_kinds_and_use_known_domains() -> None:
    registry = _mutable()
    registry.formats["unity-project"]["kinds"] = []
    registry.formats["wav"]["domain"] = "nowhere"
    registry.formats["svg"]["text"] = "maybe"
    text = _errors(registry)
    assert "kind game.unity-project: no format stores it" in text
    assert "format wav: unknown domain 'nowhere'" in text
    assert "format svg: text must be one of" in text


def test_resolve_accepts_ids_extensions_and_effects() -> None:
    assert resolve_node(REGISTRY, "stl") == ["stl"]
    assert resolve_node(REGISTRY, ".scad") == ["openscad"]
    assert resolve_node(REGISTRY, "effect.printed-part") == ["effect.printed-part"]
    assert "ifc" in resolve_node(REGISTRY, ".ifc")
    assert resolve_node(REGISTRY, ".nope") == []
    assert resolve_node(REGISTRY, "product.yaml") == ["product-spec"]
    assert resolve_node(REGISTRY, "Dockerfile") == ["dockerfile"]


def test_find_paths_prefers_implemented_routes_and_stops_at_restricted_domains() -> None:
    [route, *_] = find_paths(REGISTRY, "srt", "delivery-package", statuses=["implemented"])
    assert all(step["status"] == "implemented" for step in route)
    assert route[0]["converter"] == "captions-to-marketing"
    printed = find_paths(REGISTRY, "openscad", "effect.printed-part")
    assert [step["converter"] for step in printed[0]] == ["openscad", "slice", "print-3d"]
    assert printed[0][-1]["approval"] == "human"
    assert find_paths(REGISTRY, "hdl", "effect.device-flashed") == []
    assert find_paths(REGISTRY, "robot-description", "effect.machined-part") == []


def test_cli_check_formats_and_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    assert "formats.v1" in capsys.readouterr().out
    assert main(["formats", "--domain", "electronics"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {row["format"] for row in listed["formats"]} >= {"kicad", "fab-package", "circuit-code"}
    assert main(["path", ".scad", "effect.printed-part", "--no-detect"]) == 0
    routes = json.loads(capsys.readouterr().out)["routes"]
    assert routes[0]["needs_approval"] is True and "prusaslicer" in routes[0]["requires"]
    assert routes[0]["ready"] is False
    assert main(["path", ".srt", "delivery-package", "--implemented-only", "--no-detect"]) == 0
    assert json.loads(capsys.readouterr().out)["routes"][0]["ready"] is True
    assert main(["path", "hdl", "effect.device-flashed", "--no-detect"]) == 1
    capsys.readouterr()
    assert main(["path", ".nope", "pdf"]) == 2


def test_every_required_runtime_is_registered_and_detectable() -> None:
    registry = _mutable()
    _converter(registry, "pandoc")["requires"] = ["pandoc", "nonexistent-tool"]
    registry.runtimes["orphan"] = {"label": "x", "commands": ["x"]}
    registry.runtimes["typst"] = {"label": "Typst", "commands": ["typst"], "manual": True}
    text = _errors(registry)
    assert "runtime nonexistent-tool: required by a converter but missing" in text
    assert "runtime orphan: no converter requires it" in text
    assert "runtime typst: use detection probes or manual" in text


def test_detect_runtime_uses_env_path_globs_and_manual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = tmp_path / "tools" / "v2" / "slicer.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"x")
    monkeypatch.setenv("FAKE_TOOL_BIN", str(tool))
    assert detect_runtime({"env": ["FAKE_TOOL_BIN"]})["found"] == str(tool)
    glob_hit = detect_runtime({"commands": ["surely-not-on-path-123"], "paths": [str(tmp_path / "tools" / "*" / "slicer.exe")]})
    assert glob_hit == {"status": "installed", "found": str(tool)}
    assert detect_runtime({"commands": ["surely-not-on-path-123"]})["status"] == "missing"
    assert detect_runtime({"manual": True})["status"] == "manual"
    assert detect_runtime({"python_modules": ["json"]}, [sys.executable])["status"] == "installed"


def test_route_report_marks_missing_runtimes() -> None:
    installed = {name: {"status": "missing"} for name in REGISTRY.runtimes}
    report = route_report(REGISTRY, ".scad", "effect.printed-part", installed=installed)
    first = report["routes"][0]
    assert first["missing"] == ["openscad", "prusaslicer"] and first["runnable_now"] is False
    installed["openscad"] = installed["prusaslicer"] = {"status": "installed"}
    assert route_report(REGISTRY, ".scad", "effect.printed-part", installed=installed)["routes"][0]["runnable_now"]
    with pytest.raises(KeyError):
        route_report(REGISTRY, ".nope", "pdf")


def test_api_formats_runtimes_and_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from production_hub import api as api_module
    from production_hub.api import ApiError, HubApi
    from production_hub.hub import Hub

    calls = []

    def fake_detect(registry: FormatRegistry) -> dict[str, dict[str, str]]:
        calls.append(1)
        return {name: {"label": name, "status": "missing"} for name in registry.runtimes}

    monkeypatch.setattr(api_module, "detect_runtimes", fake_detect)
    hub_api = HubApi(Hub(tmp_path / "home"))
    formats, _ = hub_api.get("/api/v1/formats", {})
    assert formats["summary"]["formats"] == len(REGISTRY.formats) and "effect.printed-part" in formats["effects"]
    routes, _ = hub_api.get("/api/v1/path", {"from": [".srt"], "to": ["delivery-package"], "implemented_only": ["1"]})
    assert routes["routes"][0]["ready"] is True and routes["routes"][0]["runnable_now"] is True
    hub_api.get("/api/v1/runtimes", {})
    assert len(calls) == 1
    hub_api.get("/api/v1/runtimes", {"refresh": ["1"]})
    assert len(calls) == 2
    with pytest.raises(ApiError) as missing:
        hub_api.get("/api/v1/path", {"from": [".nope"], "to": ["pdf"]})
    assert missing.value.code == "unknown_format"
    with pytest.raises(ApiError):
        hub_api.get("/api/v1/path", {"from": ["stl"]})


def test_unquoted_commas_in_flow_values_are_reported() -> None:
    registry = _mutable()
    registry.domains["fabrication"] = {"label": "ผลิต (3D print", "max_level": "L6", "CNC": None}
    _converter(registry, "pandoc")["notes"] = "typo"
    text = _errors(registry)
    assert "domain fabrication: unknown keys CNC" in text
    assert "converter pandoc: unknown keys notes" in text
