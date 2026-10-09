from __future__ import annotations

import copy
from typing import Any

import pytest

from production_hub.contracts import ContractError, validate_artifact
from production_hub.registry import load_registries

KINDS = load_registries().kinds
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "artifact.v1",
        "artifact_id": SHA_A,
        "kind": "chart.svg",
        "media_type": "image/svg+xml",
        "byte_size": 2048,
        "filename": "chart.svg",
        "producer": {"tool_id": "data-viz-v1a", "tool_version": "1.0.0", "job_id": "job-1"},
        "lineage": [SHA_B],
        "qa": {"status": "accepted", "manifest_ref": "jobs/job-1/report.json"},
        "privacy_class": "internal",
        "created_at": "2026-09-30T05:00:00Z",
    }
    record.update(overrides)
    return record


def _errors(record: dict[str, Any]) -> str:
    with pytest.raises(ContractError) as caught:
        validate_artifact(record, KINDS)
    return str(caught.value)


def test_valid_record_passes() -> None:
    validate_artifact(_record(project_id="client-a", attributes={"width": 800}), KINDS)


def test_artifact_id_must_be_sha256() -> None:
    assert "artifact_id" in _errors(_record(artifact_id="md5:abc"))


def test_filename_rejects_paths() -> None:
    for filename in ("jobs/chart.svg", "C:chart.svg", "..\\chart.svg"):
        assert "filename" in _errors(_record(filename=filename))


def test_unknown_kind_is_rejected() -> None:
    assert "unknown kind" in _errors(_record(kind="hologram.cube"))


def test_media_type_must_match_kind() -> None:
    assert "not allowed for kind" in _errors(_record(media_type="image/png"))


def test_lineage_cannot_reference_itself() -> None:
    assert "own input" in _errors(_record(lineage=[SHA_A]))


def test_lineage_entries_must_be_sha256_and_unique() -> None:
    assert "lineage" in _errors(_record(lineage=["jobs/input.csv"]))
    assert "lineage" in _errors(_record(lineage=[SHA_B, SHA_B]))


@pytest.mark.parametrize("manifest_ref", ["/abs/report.json", "C:/report.json", "../report.json", "a\\b.json"])
def test_manifest_ref_must_stay_relative(manifest_ref: str) -> None:
    assert "manifest_ref" in _errors(_record(qa={"status": "accepted", "manifest_ref": manifest_ref}))


def test_unexpected_fields_and_bad_enums_are_rejected() -> None:
    assert "additional properties" in _errors(_record(storage_path="C:/store/x")).lower()
    assert "privacy_class" in _errors(_record(privacy_class="secret"))
    record = _record()
    record["qa"] = copy.deepcopy(record["qa"]) | {"status": "maybe"}
    assert "qa/status" in _errors(record)


def test_created_at_must_be_datetime() -> None:
    assert "created_at" in _errors(_record(created_at="yesterday"))
