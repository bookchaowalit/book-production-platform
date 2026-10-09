from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PACKAGE_ROOT / "edl.schema.json"
EXAMPLE_PATH = PACKAGE_ROOT / "examples" / "initial-edl.json"


def test_schema_declares_edl_v1_top_level_contract() -> None:
    assert SCHEMA_PATH.is_file(), "EDL schema file is missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["version"]["const"] == 1
    assert set(schema["required"]) == {
        "version",
        "project_id",
        "source_assets",
        "timeline",
        "assets",
        "output",
        "capabilities",
    }


def test_initial_example_is_json_and_has_required_contract_fields() -> None:
    assert EXAMPLE_PATH.is_file(), "initial EDL example is missing"
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert example["version"] == 1
    assert example["timeline"]
    assert example["capabilities"]["required"] == ["cut", "captions", "center_reframe"]
