from __future__ import annotations

import copy
from pathlib import Path

import pytest

from production_hub.cli import main
from production_hub.registry import Registries, check_registries, load_catalog_tool_ids, load_registries

REGISTRIES = load_registries()
CATALOG_IDS = load_catalog_tool_ids()


def _mutable() -> Registries:
    return copy.deepcopy(REGISTRIES)


def _edge(registries: Registries, edge_id: str) -> dict:  # type: ignore[type-arg]
    return next(edge for edge in registries.handoffs if edge["id"] == edge_id)


def test_repository_registries_are_consistent() -> None:
    assert check_registries(REGISTRIES, CATALOG_IDS) == []


def test_every_catalog_tool_has_io_entry() -> None:
    assert set(REGISTRIES.tools) == set(CATALOG_IDS)
    assert len(CATALOG_IDS) == 15


def test_existing_pipelines_are_registered_as_implemented() -> None:
    implemented = {edge["id"] for edge in REGISTRIES.handoffs if edge["status"] == "implemented"}
    assert implemented == {
        "T6.1-scene-to-graphics",
        "T6.2-video-to-audio",
        "T6.2-transcript-to-documents",
        "T6.3-dataset-to-spreadsheet",
        "T6.3-dataset-to-documents",
        "E1-master-to-video",
        "E2-captions-to-marketing",
        "E3-graphic-to-video",
        "E4-chart-to-graphics",
        "E5-chart-to-documents",
        "E6-copy-to-graphics",
        "E7a-copy-to-web",
        "E7b-graphic-to-web",
        "E9-cad-to-scene",
        "E10-cad-to-documents",
        "E11-takeoff-to-spreadsheet",
        "E12b-audio-to-game",
    }


def test_missing_and_extra_tools_are_reported() -> None:
    registries = _mutable()
    del registries.tools["game-v1a"]
    registries.tools["hologram-v1"] = copy.deepcopy(registries.tools["web-v1a"])
    errors = "\n".join(check_registries(registries, CATALOG_IDS))
    assert "without an entry: game-v1a" in errors
    assert "not in the catalog: hologram-v1" in errors


def test_unknown_kind_in_tool_io_is_reported() -> None:
    registries = _mutable()
    registries.tools["web-v1a"]["produces"].append("web.hologram")
    assert any("unknown kind 'web.hologram'" in error for error in check_registries(registries, CATALOG_IDS))


def test_kind_cannot_be_both_shipped_and_planned() -> None:
    registries = _mutable()
    registries.tools["graphics-v1a"]["planned_produces"].append("graphic.png")
    assert any("both produces and planned_produces" in e for e in check_registries(registries, CATALOG_IDS))


def test_implemented_edge_requires_shipped_io() -> None:
    registries = _mutable()
    _edge(registries, "E12a-model-to-game")["status"] = "implemented"
    _edge(registries, "E12a-model-to-game")["request_contract"] = "game-ir.v1"
    _edge(registries, "E12a-model-to-game")["evidence"] = "services/production-pipelines/README.md"
    errors = check_registries(registries, CATALOG_IDS)
    assert any("game-v1a does not consume model3d.glb" in error for error in errors)


def test_planned_edge_requires_planned_io() -> None:
    registries = _mutable()
    registries.tools["game-v1a"]["planned_consumes"].remove("model3d.glb")
    assert any("does not consume model3d.glb" in e for e in check_registries(registries, CATALOG_IDS))


def test_implemented_edge_needs_contract_and_evidence(tmp_path: Path) -> None:
    registries = _mutable()
    edge = _edge(registries, "T6.1-scene-to-graphics")
    edge["request_contract"] = "tbd"
    edge["evidence"] = "docs/missing.md"
    errors = "\n".join(check_registries(registries, CATALOG_IDS, repo_root=tmp_path))
    assert "versioned request_contract" in errors
    assert "existing evidence file" in errors


def test_duplicate_ids_edges_and_bad_status_are_reported() -> None:
    registries = _mutable()
    registries.handoffs.append(copy.deepcopy(registries.handoffs[0]))
    registries.handoffs.append({**copy.deepcopy(registries.handoffs[1]), "id": "x", "status": "maybe"})
    errors = "\n".join(check_registries(registries, CATALOG_IDS))
    assert "duplicate id" in errors
    assert "duplicate edge" in errors
    assert "status must be one of" in errors


def test_unknown_tools_and_self_edges_are_reported() -> None:
    registries = _mutable()
    edge = _edge(registries, "E9-cad-to-scene")
    edge["to_tool"] = "cad-v1a"
    registries.handoffs.append({**copy.deepcopy(edge), "id": "y", "from_tool": "ghost", "to_tool": "ghost-2"})
    errors = "\n".join(check_registries(registries, CATALOG_IDS))
    assert "two different tools" in errors
    assert "unknown from_tool 'ghost'" in errors
    assert "unknown to_tool 'ghost-2'" in errors


def test_cli_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    assert capsys.readouterr().out.startswith("OK: artifact.v1 schema valid;")


def test_missing_catalog_names_the_workspace_override(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="SOLO_EMPIRE_ROOT"):
        load_catalog_tool_ids(tmp_path / "catalog" / "production-tools.yaml")
