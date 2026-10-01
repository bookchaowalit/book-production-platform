from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_ir import (
    GameInputError,
    canonical_game_ir_bytes,
    parse_game_ir,
    to_unity_worker_json,
    unity_asset_path,
)

V1: dict[str, Any] = {
    "schema_version": "game-ir.v1",
    "scene_id": "audio-room",
    "template": "top-down-collect.v1",
    "world": {"width": 6, "height": 5, "tile_size": 1.0},
    "player": {"id": "player", "spawn": [1, 1], "speed": 5.0},
    "walls": [],
    "objectives": [{"id": "coin-a", "cell": [3, 1]}],
    "exit": {"id": "door", "cell": [4, 3]},
}
SHA = "a" * 64


def v2(**overrides: Any) -> dict[str, Any]:
    value = {
        **V1,
        "schema_version": "game-ir.v2",
        "assets": [
            {"asset_id": "sfx", "kind": "audio", "filename": "coin.ogg", "sha256": "b" * 64},
            {"asset_id": "music", "kind": "audio", "filename": "theme.wav", "sha256": SHA},
        ],
        "audio": {"music": "music", "collect_sfx": "sfx"},
    }
    value.update(overrides)
    return value


def parse(value: dict[str, Any]) -> Any:
    return parse_game_ir(json.dumps(value).encode("utf-8"))


def test_v1_canonical_bytes_do_not_gain_v2_fields() -> None:
    encoded = json.loads(canonical_game_ir_bytes(parse(V1)))
    assert "assets" not in encoded and "audio" not in encoded
    worker = json.loads(to_unity_worker_json(parse(V1)))
    assert "assets" not in worker and "audio" not in worker


def test_v1_rejects_assets_and_audio() -> None:
    with pytest.raises(GameInputError):
        parse({**V1, "audio": {"music": None, "collect_sfx": None}})


def test_v2_sorts_assets_and_projects_unity_paths() -> None:
    game = parse(v2())
    encoded = json.loads(canonical_game_ir_bytes(game))
    assert [asset["asset_id"] for asset in encoded["assets"]] == ["music", "sfx"]
    worker = json.loads(to_unity_worker_json(game))
    assert worker["audio"] == {"music": "music", "collect_sfx": "sfx"}
    assert worker["assets"][0]["unity_path"] == "Assets/BookProduction/Audio/music.wav"
    sfx = next(asset for asset in game.assets if asset.asset_id == "sfx")
    assert unity_asset_path(sfx) == "Assets/BookProduction/Audio/sfx.ogg"


def test_v2_without_audio_is_valid_and_encodes_empty_refs() -> None:
    worker = json.loads(to_unity_worker_json(parse(v2(assets=[], audio=None))))
    assert worker["audio"] == {"music": "", "collect_sfx": ""}


@pytest.mark.parametrize(
    "overrides",
    [
        {"audio": {"music": "missing", "collect_sfx": None}},
        {"assets": [{"asset_id": "a", "kind": "audio", "filename": "a.wav", "sha256": SHA}] * 2},
        {"assets": [{"asset_id": "a", "kind": "audio", "filename": "../a.wav", "sha256": SHA}]},
        {"assets": [{"asset_id": "a", "kind": "audio", "filename": "a.mp3", "sha256": SHA}]},
        {"assets": [{"asset_id": "a", "kind": "model", "filename": "a.wav", "sha256": SHA}]},
        {"assets": [{"asset_id": "a", "kind": "audio", "filename": "a.wav", "sha256": "xyz"}]},
        {"assets": [{"asset_id": f"a{i}", "kind": "audio", "filename": f"a{i}.wav", "sha256": SHA} for i in range(5)]},
    ],
)
def test_v2_rejects_bad_assets(overrides: dict[str, Any]) -> None:
    with pytest.raises(GameInputError):
        parse(v2(**overrides))


def test_v2_schema_documents_assets() -> None:
    schema_path = (
        Path(__file__).parents[2] / "src" / "book_production_platform" / "game_ir" / "game-ir.v2.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "game-ir.v2"
    assert schema["properties"]["assets"]["maxItems"] == 4
    assert schema["$defs"]["AssetIR"]["properties"]["kind"]["const"] == "audio"


@pytest.mark.parametrize("reserved", ["con", "nul", "aux", "prn", "com1", "lpt9"])
def test_ids_that_become_file_names_reject_windows_device_names(reserved: str) -> None:
    # scene_id names Assets/Scenes/<id>.unity and the player build; asset_id names
    # Assets/BookProduction/Audio/<id>.<ext>. Windows cannot create either file.
    with pytest.raises(GameInputError):
        parse(v2(scene_id=reserved))
    asset = {"asset_id": reserved, "kind": "audio", "filename": "a.wav", "sha256": SHA}
    with pytest.raises(GameInputError):
        parse(v2(assets=[asset], audio=None))


def test_ids_that_only_start_with_a_device_name_are_still_valid() -> None:
    game = parse(v2(scene_id="console-room", assets=[
        {"asset_id": "con-theme", "kind": "audio", "filename": "a.wav", "sha256": SHA}], audio=None))
    assert game.scene_id == "console-room"
