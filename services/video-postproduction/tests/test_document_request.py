import json
from datetime import date

import jsonschema
import pytest
from synthetic_fixtures import synthetic_artifact_payload, synthetic_document_request

from video_postpipeline.contract import parse_transcript_artifact
from video_postpipeline.document_request import (
    DocumentRequestError,
    TranscriptDocumentRequest,
    build_document_request,
    resolve_locale,
    validate_document_request_payload,
    write_document_request,
)
from video_postpipeline.paths import SCHEMAS_DIR


def _artifact():
    return parse_transcript_artifact(synthetic_artifact_payload())


def test_built_request_matches_the_expected_contract_payload():
    payload = build_document_request(_artifact(), issue_date=date(2026, 9, 26))
    assert payload == synthetic_document_request()
    assert validate_document_request_payload(payload) == []
    TranscriptDocumentRequest.model_validate(payload)


def test_emitted_request_file_validates_against_the_embedded_schema(tmp_path):
    target = write_document_request(_artifact(), tmp_path / "request.json", issue_date=date(2026, 9, 26))
    payload = json.loads(target.read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS_DIR / "transcript-document-request.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def test_document_id_and_title_overrides_are_honored():
    payload = build_document_request(
        _artifact(),
        issue_date=date(2026, 9, 26),
        document_id="meeting-a",
        title="บทถอดเสียง การประชุม",
        locale="th-TH",
    )
    assert payload["document_id"] == "meeting-a"
    assert payload["title"] == "บทถอดเสียง การประชุม"


def test_locale_resolution_is_deterministic():
    assert resolve_locale("th") == "th-TH"
    assert resolve_locale("EN") == "en-US"
    assert resolve_locale("fr") == "fr"
    assert resolve_locale(None) == "th-TH"


def test_missing_source_name_is_a_loud_error():
    payload = synthetic_artifact_payload()
    payload.pop("source_name")
    with pytest.raises(DocumentRequestError, match="source_name"):
        build_document_request(parse_transcript_artifact(payload), issue_date=date(2026, 9, 26))


def test_invalid_locale_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_document_request(_artifact(), tmp_path / "request.json", issue_date=date(2026, 9, 26), locale="x")
