"""Trace, history, and manual guide for BIM takeoff jobs (worker stubbed).

The Bonsai / Calc click paths are checked at the spec level only; neither
program runs in this test environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_ir import (
    SUPPORTED_ELEMENT_CLASSES,
    parse_bim_takeoff_ir,
)
from book_production_platform.bim_ir.models import SupportedElementClass
from book_production_platform.bim_production import jobs as jobs_module
from book_production_platform.bim_production import worker as bim_worker
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.jobs import (
    JOB_ARTIFACTS,
    build_bim_takeoff_job,
)
from book_production_platform.bim_production.manual import build_takeoff_guide
from book_production_platform.bim_production.runtime import BimWorkerResult
from book_production_platform.production_trace import (
    read_history,
    render_manual_markdown,
    verify_trace,
)
from tests.bim_production.test_jobs import (
    IR_JSON,
    SYNTHETIC_IFC,
    _identity,
    _records_complete,
    _records_needs_input,
    _worker_result,
)

CANARY = "CANARY-client-house-91c2"


def _records_rich() -> list[dict[str, Any]]:
    records = _records_complete()
    records.append({**records[0], "global_id": "0" * 21 + "2b", "raw_value": "1250.0"})
    records.append(
        {
            "global_id": "0" * 21 + "3c", "ifc_class": "IfcWallStandardCase", "qto_name": None,
            "quantity_name": None, "measure_kind": None, "method": None, "raw_value": None, "unit": None,
            "gap": "quantity_missing",
        }
    )
    return records


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / CANARY
    folder.mkdir()
    ir_path = folder / "ir.bimreq"
    ir_path.write_text(json.dumps(IR_JSON), encoding="utf-8")
    ifc = folder / f"{CANARY}.ifc"
    ifc.write_text(SYNTHETIC_IFC, encoding="ascii")
    return ir_path, ifc


def _stub(monkeypatch: pytest.MonkeyPatch, result: BimWorkerResult) -> None:
    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", _identity)
    monkeypatch.setattr(jobs_module, "run_bim_worker", lambda *args, **kwargs: result)


def _trace(output: Path, job_id: str) -> dict[str, Any]:
    return json.loads((output / ".trace" / "jobs" / job_id / "trace.json").read_text(encoding="utf-8"))


def test_partial_takeoff_records_trace_manual_history_and_verifies(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, _worker_result(_records_rich()))
    output = tmp_path / "out"
    result = build_bim_takeoff_job(*inputs, output, Path("FreeCADCmd.exe"), Path("sp"))

    assert result.status == "partial" and result.trace_job_id is not None
    trace = _trace(output, result.trace_job_id)
    assert trace["tool_id"] == "bim-v1a" and trace["status"] == "accepted"
    assert trace["qa"] == {"status": "partial", "checks": 1}
    stages = [step["stage"] for step in trace["steps"]]
    assert stages == ["validate", "validate", "fingerprint", "replay", "runtime", "worker", "extract", "report", "qa", "publish"]
    worker = next(step for step in trace["steps"] if step["stage"] == "worker")
    assert worker["params"]["element_classes"] == ["IfcWall"] and "ifc_path" not in worker["params"]
    assert {Path(item["file"]).name for item in trace["artifacts"]} == {"ir.json", *JOB_ARTIFACTS}
    assert [item["name"] for item in trace["inputs"]] == ["bim_takeoff_ir", "ifc_source"]
    [entry] = read_history(output / ".trace")
    assert entry["status"] == "accepted" and entry["job_id"] == result.trace_job_id

    report = json.loads((output / "jobs" / str(result.fingerprint) / "takeoff.json").read_text(encoding="utf-8"))
    manual = (output / ".trace" / "jobs" / result.trace_job_id / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    for row in report["rows"]:
        assert f"{row['value']} {row['unit']}" in manual and row["global_id"] in manual
    for aggregate in report["aggregates"]:
        assert f"{aggregate['value']} {aggregate['unit']}" in manual
    assert "quantity_missing × 1" in manual

    assert verify_trace(output / ".trace", result.trace_job_id)["ok"] is True
    (output / "jobs" / str(result.fingerprint) / "takeoff.csv").write_bytes(b"tampered")
    assert verify_trace(output / ".trace", result.trace_job_id)["ok"] is False


def test_replay_and_needs_input_are_traced(inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _worker_result(_records_complete()))
    output = tmp_path / "out"
    first = build_bim_takeoff_job(*inputs, output, Path("FreeCADCmd.exe"), Path("sp"))
    replay = build_bim_takeoff_job(*inputs, output, Path("FreeCADCmd.exe"), Path("sp"))
    assert (first.status, replay.status) == ("complete", "replayed")
    assert _trace(output, str(replay.trace_job_id))["status"] == "replayed"

    other = tmp_path / "other"
    _stub(monkeypatch, _worker_result(_records_needs_input()))
    needs = build_bim_takeoff_job(*inputs, other, Path("FreeCADCmd.exe"), Path("sp"))
    assert needs.status == "needs_input"
    trace = _trace(other, str(needs.trace_job_id))
    assert trace["status"] == "needs_input"
    assert [Path(item["file"]).name for item in trace["artifacts"]] == ["ir.json"]


def test_worker_failure_and_raised_errors_are_traced_without_masking(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, BimWorkerResult(0, 1.0, 10, "worker_deadline_exceeded", None))
    output = tmp_path / "out"
    result = build_bim_takeoff_job(*inputs, output, Path("FreeCADCmd.exe"), Path("sp"))
    assert result.status == "rejected" and result.error_code == "worker_deadline_exceeded"
    trace = _trace(output, str(result.trace_job_id))
    assert trace["status"] == "failed" and trace["steps"][-1]["note"] == "worker_deadline_exceeded"

    def broken(*_args: object) -> None:
        raise BimProductionError("unsupported_ifcopenshell_runtime")

    monkeypatch.setattr(jobs_module, "inspect_bim_runtime", broken)
    raised = tmp_path / "raised"
    with pytest.raises(BimProductionError) as error:
        build_bim_takeoff_job(*inputs, raised, Path("FreeCADCmd.exe"), Path("sp"))
    assert error.value.code == "unsupported_ifcopenshell_runtime"
    [entry] = read_history(raised / ".trace")
    assert entry["status"] == "failed"
    failed = _trace(raised, entry["job_id"])
    assert failed["steps"][-1]["note"] == "unsupported_ifcopenshell_runtime"


def test_invalid_ir_writes_no_trace(tmp_path: Path) -> None:
    bad = tmp_path / "bad.bimreq"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(BimProductionError):
        build_bim_takeoff_job(bad, tmp_path / "m.ifc", tmp_path / "out", Path("FreeCADCmd.exe"), Path("sp"))
    assert not (tmp_path / "out").exists()


def test_trace_and_history_hold_no_private_names(inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _worker_result(_records_complete()))
    output = tmp_path / "out"
    result = build_bim_takeoff_job(*inputs, output, Path("FreeCADCmd.exe"), Path("sp"))
    for path in (output / ".trace" / "history.jsonl", output / ".trace" / "jobs" / str(result.trace_job_id) / "trace.json"):
        text = path.read_text(encoding="utf-8")
        assert CANARY not in text and str(tmp_path) not in text


def test_guide_lists_requested_classes_and_pivot_key_without_report() -> None:
    ir = parse_bim_takeoff_ir({**IR_JSON, "element_classes": ["IfcWall", "IfcSpace", "IfcRoof"]})
    guide = build_takeoff_guide(ir)
    text = render_manual_markdown(guide)
    for name in ("IfcWall", "IfcSpace", "IfcRoof"):
        assert any(step.value == name for step in guide.steps)
    assert any(step.value == "ifc_class, qto_name, quantity_name, measure_kind, unit, method" for step in guide.steps)
    assert "Insert ▸ Pivot Table…" in text and "needs_input" in text
    assert guide.program.startswith("Bonsai")


def test_ir_schema_and_worker_class_allowlists_stay_in_sync() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "src" / "book_production_platform" / "bim_ir" / "bim-takeoff.v1.schema.json").read_text(encoding="utf-8")
    )
    items = schema["properties"]["element_classes"]
    assert tuple(items["items"]["enum"]) == SUPPORTED_ELEMENT_CLASSES == bim_worker.SUPPORTED_ELEMENT_CLASSES
    assert items["maxItems"] == len(SUPPORTED_ELEMENT_CLASSES) == 18
    assert set(SupportedElementClass.__args__) == set(SUPPORTED_ELEMENT_CLASSES)  # type: ignore[attr-defined]
    for name in ("IfcSpace", "IfcRoof", "IfcCovering", "IfcFooting", "IfcCurtainWall", "IfcStair"):
        assert parse_bim_takeoff_ir({**IR_JSON, "element_classes": [name]}).element_classes == (name,)
