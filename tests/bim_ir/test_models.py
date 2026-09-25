from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.bim_ir.conftest import ALL_ELEMENT_CLASSES, BASE_BIM_TAKEOFF_IR


def _ir_module() -> Any:
    spec = importlib.util.find_spec("book_production_platform.bim_ir.models")
    assert spec is not None, "BimTakeoffIR contract module must exist for takeoff jobs"
    module = importlib.import_module("book_production_platform.bim_ir.models")
    return module


def test_parse_accepts_a_valid_minimal_request(
    valid_bim_takeoff_ir: dict[str, Any],
) -> None:
    module = _ir_module()
    ir = module.parse_bim_takeoff_ir(valid_bim_takeoff_ir)

    assert isinstance(ir, module.BimTakeoffIR)
    assert ir.schema_version == "bim-takeoff.v1"
    assert ir.takeoff_id == "walls-demo"
    assert ir.element_classes == ("IfcWall",)
    assert ir.output_units == "SI"


def test_parse_accepts_every_allowlisted_element_class() -> None:
    module = _ir_module()

    for class_name in ALL_ELEMENT_CLASSES:
        ir = module.parse_bim_takeoff_ir(
            {
                "schema_version": "bim-takeoff.v1",
                "takeoff_id": "probe",
                "element_classes": [class_name],
                "output_units": "SI",
            }
        )
        assert ir.element_classes == (class_name,)


def test_parse_accepts_all_element_classes_together() -> None:
    module = _ir_module()

    ir = module.parse_bim_takeoff_ir(
        {
            "schema_version": "bim-takeoff.v1",
            "takeoff_id": "full-model",
            "element_classes": ALL_ELEMENT_CLASSES,
            "output_units": "SI",
        }
    )

    assert set(ir.element_classes) == set(ALL_ELEMENT_CLASSES)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), "bim-takeoff.v2"),
        (("takeoff_id",), "../walls"),
        (("takeoff_id",), ""),
        (("element_classes",), ["IfcWall", "IfcWall"]),
        (("element_classes",), []),
        (("element_classes",), ["IfcWall", "IfcRoof"]),
        (("element_classes",), ["ifcwall"]),
        (("element_classes",), [120]),
        (("element_classes",), [True]),
        (("element_classes",), [None]),
        (("element_classes",), "IfcWall"),
        (("output_units",), "si"),
        (("output_units",), "MM"),
    ],
)
def test_parse_rejects_invalid_contract_values(
    valid_bim_takeoff_ir: dict[str, Any], path: tuple[str, ...], replacement: object
) -> None:
    module = _ir_module()
    valid_bim_takeoff_ir[path[0]] = replacement

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.parse_bim_takeoff_ir(valid_bim_takeoff_ir)

    assert error.value.code == "invalid_bim_takeoff_ir"


def test_parse_rejects_unknown_fields(valid_bim_takeoff_ir: dict[str, Any]) -> None:
    module = _ir_module()
    valid_bim_takeoff_ir["query"] = "SELECT * FROM ifcwall"

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.parse_bim_takeoff_ir(valid_bim_takeoff_ir)

    assert error.value.code == "invalid_bim_takeoff_ir"


@pytest.mark.parametrize(
    "field", ["schema_version", "takeoff_id", "element_classes", "output_units"]
)
def test_parse_rejects_missing_required_fields(
    valid_bim_takeoff_ir: dict[str, Any], field: str
) -> None:
    module = _ir_module()
    del valid_bim_takeoff_ir[field]

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.parse_bim_takeoff_ir(valid_bim_takeoff_ir)

    assert error.value.code == "invalid_bim_takeoff_ir"


@pytest.mark.parametrize("value", ["{}", "[]", 120, None, "IfcWall"])
def test_parse_rejects_non_object_documents(value: object) -> None:
    module = _ir_module()

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.parse_bim_takeoff_ir(value)

    assert error.value.code == "invalid_bim_takeoff_ir"


def test_models_are_frozen_and_reject_extra_fields() -> None:
    module = _ir_module()
    ir = module.parse_bim_takeoff_ir(dict(BASE_BIM_TAKEOFF_IR))

    with pytest.raises(ValidationError):
        ir.takeoff_id = "renamed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        module.BimTakeoffIR.model_validate({**BASE_BIM_TAKEOFF_IR, "url": "https://x"})


def test_canonical_bytes_are_key_order_invariant_and_newline_terminated() -> None:
    module = _ir_module()
    reordered = {
        "output_units": BASE_BIM_TAKEOFF_IR["output_units"],
        "element_classes": BASE_BIM_TAKEOFF_IR["element_classes"],
        "takeoff_id": BASE_BIM_TAKEOFF_IR["takeoff_id"],
        "schema_version": BASE_BIM_TAKEOFF_IR["schema_version"],
    }
    left = module.canonical_bim_takeoff_ir(
        module.parse_bim_takeoff_ir(BASE_BIM_TAKEOFF_IR)
    )
    right = module.canonical_bim_takeoff_ir(module.parse_bim_takeoff_ir(reordered))

    assert left == right
    assert left.endswith(b"\n")
    assert left.count(b"\n") == 1
    assert left.decode("utf-8").strip() == json.dumps(
        json.loads(left),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_load_accepts_a_valid_file(tmp_path: Path) -> None:
    module = _ir_module()
    path = tmp_path / "takeoff.json"
    path.write_text(json.dumps(BASE_BIM_TAKEOFF_IR), encoding="utf-8")

    assert module.load_bim_takeoff_ir(path).takeoff_id == "walls-demo"


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"\xff\xfe",
        b'{"schema_version":"bim-takeoff.v1","schema_version":"bim-takeoff.v1"}',
        b'{"element_classes":[NaN]}',
        b'{"element_classes":[Infinity]}',
        b'{"takeoff_id":"x","takeoff_id":"y"}',
    ],
)
def test_load_rejects_malformed_or_ambiguous_json(
    tmp_path: Path, content: bytes
) -> None:
    module = _ir_module()
    path = tmp_path / "invalid.json"
    path.write_bytes(content)

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.load_bim_takeoff_ir(path)

    assert error.value.code == "invalid_bim_takeoff_ir"


def test_load_rejects_oversized_documents(tmp_path: Path) -> None:
    module = _ir_module()
    path = tmp_path / "oversized.json"
    encoded = json.dumps(BASE_BIM_TAKEOFF_IR).encode("utf-8")
    path.write_bytes(encoded + b" " * (64 * 1024 + 1 - len(encoded)))

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.load_bim_takeoff_ir(path)

    assert error.value.code == "invalid_bim_takeoff_ir"


def test_load_rejects_missing_files_and_never_echoes_the_path(
    tmp_path: Path,
) -> None:
    module = _ir_module()
    path = tmp_path / "private-takeoff-request.json"

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.load_bim_takeoff_ir(path)

    assert error.value.code == "invalid_bim_takeoff_ir"
    assert str(error.value) == "invalid_bim_takeoff_ir"
    assert "private-takeoff" not in str(error.value)


def test_load_rejects_symlinked_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _ir_module()
    target = tmp_path / "target.json"
    target.write_text(json.dumps(BASE_BIM_TAKEOFF_IR), encoding="utf-8")
    link = tmp_path / "request.json"
    link.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == link)

    with pytest.raises(module.BimTakeoffIRError) as error:
        module.load_bim_takeoff_ir(link)

    assert error.value.code == "invalid_bim_takeoff_ir"


def test_canonical_bytes_stay_stable_across_repeated_runs() -> None:
    module = _ir_module()
    ir = module.parse_bim_takeoff_ir(BASE_BIM_TAKEOFF_IR)

    assert module.canonical_bim_takeoff_ir(ir) == module.canonical_bim_takeoff_ir(ir)
