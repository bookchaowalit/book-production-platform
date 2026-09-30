from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_production_platform.game_production import cli, export
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.export import (
    EXPORT_MANIFEST_SCHEMA,
    EXPORT_RECEIPT_SCHEMA,
    EXPORTER_VERSION,
    GameExportResult,
    export_fingerprint,
    verify_export_replay,
)
from book_production_platform.game_production.jobs import pretty_json_bytes, sha256_file


def _publish_fake_export(root: Path, fingerprint: str = "c" * 64) -> Path:
    artifact = root / fingerprint
    player = artifact / "player"
    (player / "Build").mkdir(parents=True)
    (player / "index.html").write_text("<html></html>", encoding="utf-8")
    (player / "Build" / "room.loader.js").write_text("// loader", encoding="utf-8")
    (player / "Build" / "room.wasm.gz").write_bytes(b"\x1f\x8b wasm")
    tree_hash, total, files = export._player_index(player)
    manifest = {
        "schema_version": EXPORT_MANIFEST_SCHEMA,
        "status": "accepted",
        "fingerprint": fingerprint,
        "exporter_version": EXPORTER_VERSION,
        "target": "web",
        "source_fingerprint": "a" * 64,
        "player": {"entrypoint": "index.html", "tree_sha256": tree_hash, "bytes": total, "files": files},
        "model_calls": 0,
    }
    manifest_path = artifact / "export-manifest.json"
    manifest_path.write_bytes(pretty_json_bytes(manifest))
    receipt = {
        "schema_version": EXPORT_RECEIPT_SCHEMA,
        "fingerprint": fingerprint,
        "hashes": {"player_tree_sha256": tree_hash, "export_manifest_sha256": sha256_file(manifest_path)},
    }
    (artifact / ".receipt.json").write_bytes(pretty_json_bytes(receipt))
    return artifact


def test_fingerprint_changes_with_target_and_source() -> None:
    base = export_fingerprint("a" * 64, "b" * 64, "web", "e" * 64, "f" * 64)
    assert base == export_fingerprint("a" * 64, "b" * 64, "web", "e" * 64, "f" * 64)
    assert base != export_fingerprint("a" * 64, "b" * 64, "windows", "e" * 64, "f" * 64)
    assert base != export_fingerprint("d" * 64, "b" * 64, "web", "e" * 64, "f" * 64)
    assert base != export_fingerprint("a" * 64, "b" * 64, "web", "e" * 64, "0" * 64)


@pytest.mark.parametrize(("target", "timeout_s", "code"), [
    ("android", 60, "unsupported_export_target"),
    ("web", 0, "invalid_timeout"),
    ("web", 1801, "invalid_timeout"),
    ("web", True, "invalid_timeout"),
])
def test_invalid_request_is_rejected_before_any_work(target: str, timeout_s: int, code: str, tmp_path: Path) -> None:
    with pytest.raises(GameJobError) as error:
        export.export_game_player(tmp_path / "missing", target, tmp_path / "out", tmp_path / "Unity.exe", timeout_s)
    assert error.value.code == code


def test_replay_verifies_published_export(tmp_path: Path) -> None:
    artifact = _publish_fake_export(tmp_path)

    result = verify_export_replay(artifact)

    assert result.status == "replayed"
    assert result.replay is True
    assert result.target == "web"
    assert result.entrypoint == "index.html"
    assert result.model_calls == 0


def test_replay_rejects_tampered_player_file(tmp_path: Path) -> None:
    artifact = _publish_fake_export(tmp_path)
    (artifact / "player" / "index.html").write_text("<html>changed</html>", encoding="utf-8")

    with pytest.raises(GameJobError) as error:
        verify_export_replay(artifact)
    assert error.value.code == "replay_integrity_error"


def test_replay_rejects_unexpected_root_entry(tmp_path: Path) -> None:
    artifact = _publish_fake_export(tmp_path)
    (artifact / "extra.txt").write_text("x", encoding="utf-8")

    with pytest.raises(GameJobError) as error:
        verify_export_replay(artifact)
    assert error.value.code == "replay_integrity_error"


def test_web_entrypoint_requires_loader_and_wasm(tmp_path: Path) -> None:
    player = tmp_path / "player"
    (player / "Build").mkdir(parents=True)
    (player / "index.html").write_text("", encoding="utf-8")
    with pytest.raises(GameJobError) as error:
        export._entrypoint(player, "web", "room")
    assert error.value.code == "player_incomplete"

    (player / "Build" / "player.loader.js").write_text("", encoding="utf-8")
    (player / "Build" / "player.wasm.gz").write_bytes(b"")
    with pytest.raises(GameJobError):
        export._entrypoint(player, "web", "room")

    (player / "Build" / "player.data.gz").write_bytes(b"")
    (player / "Build" / "player.framework.js.gz").write_bytes(b"")
    assert export._entrypoint(player, "web", "room") == "index.html"


def test_windows_entrypoint_and_do_not_ship_cleanup(tmp_path: Path) -> None:
    player = tmp_path / "player"
    (player / "room_Data").mkdir(parents=True)
    (player / "room_BurstDebugInformation_DoNotShip").mkdir()
    (player / "room.exe").write_bytes(b"MZ")
    (player / "UnityPlayer.dll").write_bytes(b"MZ")

    export._remove_do_not_ship(player)

    assert not (player / "room_BurstDebugInformation_DoNotShip").exists()
    assert export._entrypoint(player, "windows", "room") == "room.exe"


def test_cli_export_reports_json(tmp_path: Path, monkeypatch, capsys) -> None:
    artifact_dir = tmp_path / "export"
    expected = GameExportResult(
        "accepted", "c" * 64, "web", artifact_dir, "index.html", "a" * 64, {"player_tree_sha256": "d" * 64}, False, 0
    )
    calls: list[tuple[Path, str, Path, Path, int]] = []

    def fake_export(artifact: Path, target: str, output_root: Path, unity_bin: Path, timeout_s: int):
        calls.append((artifact, target, output_root, unity_bin, timeout_s))
        return expected

    monkeypatch.setattr(cli, "export_game_player", fake_export)

    exit_code = cli.game_main(
        [
            "export",
            "--artifact", str(tmp_path / "build"),
            "--target", "web",
            "--output-root", str(tmp_path / "exports"),
            "--unity", "Unity.exe",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "accepted"
    assert result["target"] == "web"
    assert result["entrypoint"] == "index.html"
    assert result["model_calls"] == 0
    assert calls == [(tmp_path / "build", "web", tmp_path / "exports", Path("Unity.exe"), 900)]


def test_cli_export_rejects_unknown_target(capsys) -> None:
    exit_code = cli.game_main(
        ["export", "--artifact", "a", "--target", "android", "--output-root", "o", "--unity", "u"]
    )
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "rejected", "error": "invalid_cli_arguments"}
