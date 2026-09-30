from __future__ import annotations

import hashlib
import json
import shutil
import wave
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_ir import parse_game_ir, validate_game_ir
from book_production_platform.game_production import assets, jobs
from book_production_platform.game_production.errors import GameJobError
from tests.game_production.test_jobs import game_ir, install_fake_stages, write_game


def write_wav(path: Path, seconds: float = 0.25) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(b"\x00\x01" * int(8000 * seconds))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v2(music_sha: str, sfx_sha: str | None = None) -> dict[str, Any]:
    value = {**game_ir(), "schema_version": "game-ir.v2"}
    value["assets"] = [{"asset_id": "theme", "kind": "audio", "filename": "theme.wav", "sha256": music_sha}]
    audio: dict[str, Any] = {"music": "theme", "collect_sfx": None}
    if sfx_sha:
        value["assets"].append({"asset_id": "coin", "kind": "audio", "filename": "coin.wav", "sha256": sfx_sha})
        audio["collect_sfx"] = "coin"
    value["audio"] = audio
    return value


def parsed(value: dict[str, Any]) -> Any:
    return validate_game_ir(parse_game_ir(json.dumps(value).encode("utf-8")))


def fake_probe(path: Path) -> tuple[str, float]:
    return "wav", 0.25


def test_resolve_checks_root_hash_size_and_format(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    sha = write_wav(root / "theme.wav")
    game = parsed(v2(sha))
    [item] = assets.resolve_assets(game, root, fake_probe)
    assert (item.bytes, item.format) == ((root / "theme.wav").stat().st_size, "wav")
    assert item.manifest_entry()["path"] == "project/Assets/BookProduction/Audio/theme.wav"

    with pytest.raises(GameJobError) as missing_root:
        assets.resolve_assets(game, None, fake_probe)
    assert missing_root.value.code == "asset_root_required"
    with pytest.raises(GameJobError) as mismatch:
        assets.resolve_assets(parsed(v2("0" * 64)), root, fake_probe)
    assert mismatch.value.code == "asset_hash_mismatch"
    with pytest.raises(GameJobError) as wrong_format:
        assets.resolve_assets(game, root, lambda path: ("mp3", 1.0))
    assert wrong_format.value.code == "asset_format_invalid"
    with pytest.raises(GameJobError) as too_long:
        assets.resolve_assets(game, root, lambda path: ("wav", 601.0))
    assert too_long.value.code == "asset_too_large"
    (root / "theme.wav").unlink()
    with pytest.raises(GameJobError) as gone:
        assets.resolve_assets(game, root, fake_probe)
    assert gone.value.code == "asset_unavailable"


def test_v1_and_asset_free_v2_need_no_root(tmp_path: Path) -> None:
    assert assets.resolve_assets(parsed(game_ir()), None) == []


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_real_ffprobe_reads_wav(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", seconds=0.5)
    format_name, duration = assets.ffprobe_audio(tmp_path / "a.wav")
    assert "wav" in format_name.split(",") and 0.4 < duration < 0.6
    (tmp_path / "b.wav").write_bytes(b"not audio")
    with pytest.raises(GameJobError) as error:
        assets.ffprobe_audio(tmp_path / "b.wav")
    assert error.value.code == "asset_format_invalid"


def test_v2_build_stages_audio_indexes_it_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assets, "ffprobe_audio", fake_probe)
    root = tmp_path / "assets"
    music_sha = write_wav(root / "theme.wav")
    sfx_sha = write_wav(root / "coin.wav", seconds=0.1)
    unity_bin, _ = install_fake_stages(monkeypatch, tmp_path)
    game_path = write_game(tmp_path / "input" / "room.json", v2(music_sha, sfx_sha))

    result = jobs.compose_game_scene(game_path, tmp_path / "out", unity_bin, asset_root=root)
    project = result.artifact_dir / "project"
    staged = project / "Assets" / "BookProduction" / "Audio"
    assert sorted(item.name for item in staged.iterdir()) == ["coin.wav", "coin.wav.meta", "theme.wav", "theme.wav.meta"]
    meta = (staged / "theme.wav.meta").read_text(encoding="utf-8")
    assert "guid: " + assets.asset_guid(parsed(v2(music_sha)).assets[0]) in meta
    manifest = json.loads((result.artifact_dir / "qa-manifest.json").read_text(encoding="utf-8"))
    assert [item["asset_id"] for item in manifest["assets"]] == ["coin", "theme"]
    assert manifest["qa"]["worker_report"]["music_source_count"] == 1
    assert manifest["qa"]["worker_report"]["collect_sound_configured"] is True

    replay = jobs.compose_game_scene(game_path, tmp_path / "out", unity_bin, asset_root=root)
    assert replay.status == "replayed"
    (staged / "theme.wav").write_bytes(b"tampered")
    with pytest.raises(GameJobError) as error:
        jobs.verify_replay_and_load_result(result.artifact_dir)
    assert error.value.code == "replay_integrity_error"


def test_v1_manifest_has_no_asset_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity_bin, _ = install_fake_stages(monkeypatch, tmp_path)
    game_path = write_game(tmp_path / "input" / "room.json")
    result = jobs.compose_game_scene(game_path, tmp_path / "out", unity_bin)
    manifest = json.loads((result.artifact_dir / "qa-manifest.json").read_text(encoding="utf-8"))
    assert "assets" not in manifest
    assert not (result.artifact_dir / "project" / "Assets" / "BookProduction" / "Audio").exists()
