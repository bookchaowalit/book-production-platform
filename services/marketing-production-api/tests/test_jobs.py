from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from marketing_production.jobs import (
    IdempotencyConflict,
    ReplayIntegrityError,
    run_campaign_job,
)


def _payload(asset_root: Path) -> dict:
    return {
        "campaign_id": "campaign-01",
        "name": "Launch",
        "objective": "awareness",
        "channels": ["facebook", "email"],
        "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "en"}]},
        "assets": [
            {"asset_id": "email-copy", "kind": "copy", "channel": "email", "uri": "inline:email", "text": "=Hello"},
            {"asset_id": "social-image", "kind": "image", "channel": "facebook", "uri": "creative.png"},
        ],
        "asset_root": str(asset_root),
    }


def test_job_writes_package_manifest_lineage_and_zero_model_calls(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")

    result = run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")

    assert result.status == "accepted"
    assert result.replayed is False
    assert result.model_calls == 0
    assert result.package.filename.endswith(".campaign.json")
    assert result.package.byte_size > 0
    assert (tmp_path / "out" / result.package.filename).exists()
    manifest = (tmp_path / "out" / result.manifest_filename).read_text(encoding="utf-8")
    assert "source_refs" in manifest
    assert '"publish_enabled": false' in (tmp_path / "out" / result.package.filename).read_text(encoding="utf-8")


def test_replay_returns_same_artifact_without_writes(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    first = run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")

    replay = run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")

    assert replay.replayed is True
    assert replay.package == first.package
    assert replay.bytes_written == 0


def test_changed_input_conflicts(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")
    changed = _payload(tmp_path)
    changed["name"] = "Changed"

    with pytest.raises(IdempotencyConflict):
        run_campaign_job(changed, tmp_path / "out", "key-1")


def test_tampered_package_is_rejected_on_replay(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    first = run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")
    package_path = tmp_path / "out" / first.package.filename
    package_path.write_text(package_path.read_text(encoding="utf-8").replace('"name": "Launch"', '"name": "Tampered"'), encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")


def test_tampered_manifest_is_rejected_on_replay(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    first = run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")
    manifest_path = tmp_path / "out" / first.manifest_filename
    manifest_path.write_text(manifest_path.read_text(encoding="utf-8").replace('"status": "accepted"', '"status": "failed"'), encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_campaign_job(_payload(tmp_path), tmp_path / "out", "key-1")


def test_missing_local_asset_is_rejected_without_manifest(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    result = run_campaign_job(payload, tmp_path / "out", "key-1")

    assert result.status == "rejected"
    assert result.error_code == "asset_missing"
    assert not list((tmp_path / "out").glob("manifest-*.json"))


def test_duplicate_threads_share_one_accepted_package(tmp_path: Path) -> None:
    (tmp_path / "creative.png").write_bytes(b"image-bytes")
    payload = _payload(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_campaign_job(payload, tmp_path / "out", "key-1"), range(2)))

    assert {result.status for result in results} == {"accepted"}
    assert sum(result.replayed for result in results) == 1
    assert len({result.package.filename for result in results if result.package}) == 1
