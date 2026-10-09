from typing import Any

import pytest
from synthetic_fixtures import synthetic_artifact_payload

from video_postpipeline.contract import (
    TRANSCRIPT_ARTIFACT_SCHEMA_ID,
    ValidationError,
    load_schema,
    load_transcript_artifact,
    parse_transcript_artifact,
    validate_transcript_artifact_payload,
)
from video_postpipeline.paths import SCHEMAS_DIR


def test_valid_fixture_passes_both_schema_and_model():
    payload = synthetic_artifact_payload()
    assert validate_transcript_artifact_payload(payload) == []
    artifact = parse_transcript_artifact(payload)
    assert artifact.transcript_id == "demo-001"
    assert artifact.segments[1].speaker == "Host"


def test_schema_contract_publishes_expected_identity():
    schema = load_schema()
    assert schema["$id"] == TRANSCRIPT_ARTIFACT_SCHEMA_ID
    assert schema["properties"]["schema_version"]["const"] == "transcript-artifact.v1"


def _set_top(payload: dict[str, Any], key: str, value: Any) -> None:
    payload[key] = value


def _set_first_segment(payload: dict[str, Any], key: str, value: Any) -> None:
    payload["segments"][0][key] = value


def _drop_top(payload: dict[str, Any], key: str) -> None:
    payload.pop(key, None)


def _drop_first_segment_key(payload: dict[str, Any], key: str) -> None:
    payload["segments"][0].pop(key, None)


def _remove_segments(payload: dict[str, Any]) -> None:
    payload["segments"] = []


SCHEMA_REJECTIONS: list[tuple[str, Any]] = [
    ("unknown_schema_version", lambda p: _set_top(p, "schema_version", "transcript-artifact.v2")),
    ("missing_transcript_id", lambda p: _drop_top(p, "transcript_id")),
    ("bad_transcript_id", lambda p: _set_top(p, "transcript_id", "bad id!")),
    ("extra_property", lambda p: _set_top(p, "extra", 1)),
    ("empty_segments", lambda p: _remove_segments(p)),
    ("segment_missing_end", lambda p: _drop_first_segment_key(p, "end_seconds")),
    ("empty_segment_text", lambda p: _set_first_segment(p, "text", "")),
    ("negative_start", lambda p: _set_first_segment(p, "start_seconds", -1.0)),
    ("path_in_source_name", lambda p: _set_top(p, "source_name", "C:/evil/demo.mp4")),
]

MODEL_REJECTIONS = [name for name, _ in SCHEMA_REJECTIONS]


def _mutated(name: str) -> dict[str, Any]:
    payload = synthetic_artifact_payload()
    for candidate, mutator in SCHEMA_REJECTIONS:
        if candidate == name:
            mutator(payload)
            return payload
    raise AssertionError(f"unknown case: {name}")


@pytest.mark.parametrize("name", [name for name, _ in SCHEMA_REJECTIONS])
def test_structural_violations_rejected_by_schema(name):
    assert validate_transcript_artifact_payload(_mutated(name)) != []


@pytest.mark.parametrize("name", MODEL_REJECTIONS)
def test_structural_violations_rejected_by_model(name):
    with pytest.raises(ValidationError):
        parse_transcript_artifact(_mutated(name))


def test_segment_ordering_is_a_semantic_rule_beyond_the_schema():
    payload = synthetic_artifact_payload()
    payload["segments"][0] = {"start_seconds": 2.5, "end_seconds": 2.5, "text": "เสียงเดียวกัน"}
    assert validate_transcript_artifact_payload(payload) == []
    with pytest.raises(ValidationError, match="end_seconds"):
        parse_transcript_artifact(payload)


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(ValidationError, match="cannot read"):
        load_transcript_artifact(tmp_path / "missing.json")


def test_foreign_schema_copies_match_their_owners():
    """Embedded contract copies stay byte-identical to the owning repositories."""

    documents_owner = SCHEMAS_DIR.parents[1] / "document-production-api" / "schemas" / "transcript-document-request.v1.schema.json"
    audio_owner = (
        SCHEMAS_DIR.parents[2]
        / "projects"
        / "product"
        / "engineering"
        / "book-dev"
        / "github"
        / "bookchaowalit"
        / "book-apps"
        / "tools"
        / "book-audio-production"
        / "schemas"
        / "audio-batch.v1.schema.json"
    )
    documents_copy = SCHEMAS_DIR / "transcript-document-request.v1.schema.json"
    audio_copy = SCHEMAS_DIR / "audio-batch.v1.schema.json"
    if documents_owner.exists():
        assert documents_copy.read_bytes() == documents_owner.read_bytes()
    else:  # pragma: no cover - owner relocated
        pytest.skip("document-production-api schema owner is not present")
    if audio_owner.exists():
        assert audio_copy.read_bytes() == audio_owner.read_bytes()
    else:  # pragma: no cover - owner relocated
        pytest.skip("book-audio-production schema owner is not present")
