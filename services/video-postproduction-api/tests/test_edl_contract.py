from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

try:
    from video_postproduction.edl import EDLCapabilityError, schema_is_compatible, validate_edl
except ImportError:
    EDLCapabilityError = RuntimeError
    schema_is_compatible = None  # type: ignore[assignment]
    validate_edl = None  # type: ignore[assignment]


EXAMPLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "video-postproduction-edl"
    / "examples"
    / "initial-edl.json"
)


def base_document() -> dict[str, object]:
    project_id = uuid4()
    asset_id = uuid4()
    return {
        "version": 1,
        "project_id": str(project_id),
        "source_assets": [
            {
                "asset_id": str(asset_id),
                "storage_key": "projects/project/original/asset.mp4",
                "duration": 120.0,
            }
        ],
        "timeline": [
            {
                "id": "segment_0001",
                "source": {"asset_id": str(asset_id), "start": 0.0, "end": 10.0},
                "visual": {
                    "kind": "a_roll",
                    "reframe": {"mode": "center", "aspect_ratio": "9:16"},
                    "asset_refs": [],
                },
                "captions": {
                    "enabled": True,
                    "source": "transcript",
                    "format": "ass",
                    "preset": "minimal",
                },
                "audio": {"operations": []},
                "color": {"operations": []},
                "provenance": {"reason": "test"},
            }
        ],
        "assets": [],
        "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
        "capabilities": {"required": ["cut", "captions", "center_reframe"]},
    }


def load_initial_example() -> dict[str, object]:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def require_validator():
    if validate_edl is None:
        pytest.fail("video_postproduction.edl.validate_edl is not implemented")
    return validate_edl


def require_schema_sync():
    if schema_is_compatible is None:
        pytest.fail("video_postproduction.edl.schema_is_compatible is not implemented")
    return schema_is_compatible


def test_initial_edl_is_valid() -> None:
    document = require_validator()(load_initial_example())
    assert document.version == 1


def test_edl_rejects_end_before_start() -> None:
    document = base_document()
    timeline = document["timeline"]
    assert isinstance(timeline, list)
    source = timeline[0]["source"]
    assert isinstance(source, dict)
    source["start"] = 12.0
    source["end"] = 4.0

    with pytest.raises(ValidationError):
        require_validator()(document)


def test_edl_rejects_unsupported_required_capability() -> None:
    document = base_document()
    capabilities = document["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["required"] = ["active_speaker"]

    with pytest.raises(EDLCapabilityError, match="active_speaker"):
        require_validator()(document)


def test_edl_rejects_range_outside_known_asset_duration() -> None:
    document = base_document()
    timeline = document["timeline"]
    assert isinstance(timeline, list)
    source = timeline[0]["source"]
    assert isinstance(source, dict)
    asset_id = UUID(str(source["asset_id"]))
    source["end"] = 121.0

    with pytest.raises(ValidationError):
        require_validator()(document, asset_durations={asset_id: 120.0})


def test_json_schema_matches_pydantic_contract() -> None:
    schema = json.loads(
        (EXAMPLE_PATH.parents[1] / "edl.schema.json").read_text(encoding="utf-8")
    )
    assert require_schema_sync()(schema) is True
