from __future__ import annotations

import importlib.resources
import importlib.util
import json
import re
from typing import Any

import pytest

from tests.bim_ir.conftest import ALL_ELEMENT_CLASSES, BASE_BIM_TAKEOFF_IR

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _ir_module() -> Any:
    spec = importlib.util.find_spec("book_production_platform.bim_ir.models")
    assert spec is not None, "BimTakeoffIR contract module must exist for schema tests"
    return importlib.import_module("book_production_platform.bim_ir.models")


def _load_schema() -> dict[str, Any]:
    resource = importlib.resources.files(
        "book_production_platform.bim_ir"
    ).joinpath("bim-takeoff.v1.schema.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _matches(instance: object, keyword_value: object) -> bool:
    if isinstance(keyword_value, str):
        pattern = _PATTERN_CACHE.get(keyword_value)
        if pattern is None:
            pattern = re.compile(keyword_value)
            _PATTERN_CACHE[keyword_value] = pattern
        return isinstance(instance, str) and pattern.fullmatch(instance) is not None
    return False


def schema_validate(schema: dict[str, Any], instance: object) -> bool:
    """Evaluate only the Draft 2020-12 keywords this versioned schema uses."""

    if "const" in schema and instance != schema["const"]:
        return False
    if "enum" in schema and instance not in schema["enum"]:
        return False
    expected = schema.get("type")
    if expected == "object" and not isinstance(instance, dict):
        return False
    if expected == "string" and not isinstance(instance, str):
        return False
    if expected == "array" and not isinstance(instance, list):
        return False
    if isinstance(instance, str):
        if "pattern" in schema and not _matches(instance, schema["pattern"]):
            return False
        if len(instance) < schema.get("minLength", 0):
            return False
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            return False
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            return False
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return False
        if schema.get("uniqueItems") is True:
            seen: list[object] = []
            for entry in instance:
                if entry in seen:
                    return False
                seen.append(entry)
        items = schema.get("items")
        if isinstance(items, dict) and not all(
            schema_validate(items, entry) for entry in instance
        ):
            return False
    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                return False
        properties = schema.get("properties", {})
        if (
            schema.get("additionalProperties") is False
            and any(key not in properties for key in instance)
        ):
            return False
        for key, subschema in properties.items():
            if key in instance and not schema_validate(subschema, instance[key]):
                return False
    return True


def test_json_schema_is_versioned_and_strict() -> None:
    schema = _load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "bim-takeoff.v1.schema.json"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "takeoff_id",
        "element_classes",
        "output_units",
    }
    assert schema["properties"]["schema_version"]["const"] == "bim-takeoff.v1"
    assert schema["properties"]["output_units"]["const"] == "SI"
    assert schema["properties"]["element_classes"]["uniqueItems"] is True
    assert schema["properties"]["element_classes"]["minItems"] == 1
    assert schema["properties"]["element_classes"]["items"]["enum"] == (
        ALL_ELEMENT_CLASSES
    )
    assert schema["properties"]["takeoff_id"]["pattern"] == (
        "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )


VALID_EXAMPLES = (
    dict(BASE_BIM_TAKEOFF_IR),
    {
        "schema_version": "bim-takeoff.v1",
        "takeoff_id": "full-model",
        "element_classes": ALL_ELEMENT_CLASSES,
        "output_units": "SI",
    },
    {
        "output_units": "SI",
        "element_classes": ["IfcDoor"],
        "takeoff_id": "Aa-9_",
        "schema_version": "bim-takeoff.v1",
    },
)

INVALID_EXAMPLES = (
    {**BASE_BIM_TAKEOFF_IR, "schema_version": "bim-takeoff.v2"},
    {**BASE_BIM_TAKEOFF_IR, "output_units": "mm"},
    {**BASE_BIM_TAKEOFF_IR, "takeoff_id": "-bad"},
    {**BASE_BIM_TAKEOFF_IR, "takeoff_id": "x" * 65},
    {**BASE_BIM_TAKEOFF_IR, "element_classes": []},
    {**BASE_BIM_TAKEOFF_IR, "element_classes": ["IfcWall", "IfcWall"]},
    {**BASE_BIM_TAKEOFF_IR, "element_classes": ["IfcRoof"]},
    {**BASE_BIM_TAKEOFF_IR, "element_classes": [42]},
    {**BASE_BIM_TAKEOFF_IR, "element_classes": [None]},
    {**BASE_BIM_TAKEOFF_IR, "unexpected": True},
    {key: value for key, value in BASE_BIM_TAKEOFF_IR.items() if key != "takeoff_id"},
)


@pytest.mark.parametrize(("index", "document"), list(enumerate(VALID_EXAMPLES)))
def test_schema_and_python_validator_accept_the_same_valid_examples(
    index: int, document: dict[str, Any]
) -> None:
    module = _ir_module()
    schema = _load_schema()

    assert schema_validate(schema, document) is True
    module.parse_bim_takeoff_ir(document)


@pytest.mark.parametrize(("index", "document"), list(enumerate(INVALID_EXAMPLES)))
def test_schema_and_python_validator_reject_the_same_invalid_examples(
    index: int, document: dict[str, Any]
) -> None:
    module = _ir_module()
    schema = _load_schema()

    assert schema_validate(schema, document) is False
    with pytest.raises(module.BimTakeoffIRError) as error:
        module.parse_bim_takeoff_ir(document)
    assert error.value.code == "invalid_bim_takeoff_ir"


@pytest.mark.parametrize(
    "candidate", [True, False, 12, 1.5, "text", None, [BASE_BIM_TAKEOFF_IR]]
)
def test_schema_and_python_validator_agree_on_non_object_documents(
    candidate: object,
) -> None:
    module = _ir_module()
    schema = _load_schema()

    assert schema_validate(schema, candidate) is False
    with pytest.raises(module.BimTakeoffIRError):
        module.parse_bim_takeoff_ir(candidate)


def test_schema_document_itself_is_deterministic_package_data() -> None:
    schema = _load_schema()

    assert list(schema) == [
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "additionalProperties",
        "required",
        "properties",
    ]
