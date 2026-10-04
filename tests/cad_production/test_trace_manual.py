"""Trace, history, manual guide, drawing, and analytical properties for CAD jobs.

FreeCAD is not installed here: jobs run through the fake engine from
``test_jobs`` and the guide is checked against PartIR. The FreeCAD GUI steps
themselves are unverified in this environment.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from book_production_platform.cad_ir import canonical_part_ir, parse_part_ir
from book_production_platform.cad_production import jobs
from book_production_platform.cad_production.brief_cli import _write_proposal_sidecar
from book_production_platform.cad_production.drawing import (
    dimension_labels,
    render_dxf,
    render_svg,
)
from book_production_platform.cad_production.manual import build_freecad_guide
from book_production_platform.cad_production.properties import mm, plate_properties
from book_production_platform.cad_production.runtime import WorkerResult
from book_production_platform.production_trace import (
    read_history,
    render_manual_markdown,
    sha256_json,
    verify_trace,
)
from tests.cad_production.conftest import BASE_PART_IR
from tests.cad_production.test_jobs import install_fake_engine

CANARY = "CANARY-private-7f3a"


def _part():
    return parse_part_ir(BASE_PART_IR)


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, input_dir: str = "in") -> tuple[jobs.CadJobResult, Path, Path]:
    install_fake_engine(monkeypatch)
    source_dir = tmp_path / input_dir
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "part.json"
    source.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    root = tmp_path / "out"
    result = jobs.build_cad_job(source, root, tmp_path / "FreeCADCmd.exe", 30)
    return result, root, source


def _trace(root: Path, job_id: str) -> dict[str, object]:
    return json.loads((root / ".trace" / "jobs" / job_id / "trace.json").read_text(encoding="utf-8"))


def test_accepted_job_records_trace_ir_manual_drawings_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, root, _ = _run(tmp_path, monkeypatch)

    assert result.status == "accepted" and result.trace_job_id is not None
    job_dir = root / ".trace" / "jobs" / result.trace_job_id
    assert {path.name for path in job_dir.iterdir()} == {"trace.json", "MANUAL-STEPS.md", "manual-facts.json", "ir.json", "drawing.svg", "drawing.dxf"}
    trace = _trace(root, result.trace_job_id)
    assert trace["schema_version"] == "production-trace.v1"
    assert trace["tool_id"] == "cad-v1a" and trace["status"] == "accepted"
    stages = [step["stage"] for step in trace["steps"]]
    assert stages == ["validate", "runtime", "fingerprint", "worker", "qa", "publish", "drawing"]
    worker = next(step for step in trace["steps"] if step["stage"] == "worker")
    assert worker["params"]["pad"] == {"type": "Dimension", "length_mm": "7.500"}
    assert worker["params"]["hole_sketch"]["constraints"] == 12
    assert worker["params"]["pocket"] == {"type": "ThroughAll"}
    canonical = json.loads(canonical_part_ir(_part()))
    assert json.loads((job_dir / "ir.json").read_text(encoding="utf-8")) == canonical
    assert trace["ir"] == {"schema": "part-ir.v1", "sha256": sha256_json(canonical), "file": "ir.json"}
    files = {Path(item["file"]).name: item for item in trace["artifacts"]}
    assert set(files) >= set(jobs.ARTIFACT_NAMES) | {"drawing.svg", "drawing.dxf", "ir.json"}
    for name in jobs.ARTIFACT_NAMES:
        assert files[name]["sha256"] == result.artifact_hashes[name]
    assert trace["ai"] == {"used": False, "model_calls": 0}
    assert trace["manual_guide"]["program"] == "FreeCAD"
    [entry] = read_history(root / ".trace")
    assert entry["job_id"] == result.trace_job_id and entry["status"] == "accepted"
    # The content-addressed job directory still holds exactly the five artifacts.
    assert result.artifact_dir is not None
    assert {path.name for path in result.artifact_dir.iterdir()} == set(jobs.ARTIFACT_NAMES)


def test_verify_passes_then_fails_after_artifact_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, root, _ = _run(tmp_path, monkeypatch)
    assert result.trace_job_id is not None

    assert verify_trace(root / ".trace", result.trace_job_id)["ok"] is True
    assert result.artifact_dir is not None
    (result.artifact_dir / "part.step").write_bytes(b"tampered")
    report = verify_trace(root / ".trace", result.trace_job_id)
    assert report["ok"] is False
    assert [item["file"].rsplit("/", 1)[-1] for item in report["artifacts"] if not item["ok"]] == ["part.step"]


def test_replay_records_its_own_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, root, source = _run(tmp_path, monkeypatch)
    replay = jobs.build_cad_job(source, root, tmp_path / "FreeCADCmd.exe", 30)

    assert replay.status == "replayed" and replay.trace_job_id not in {None, first.trace_job_id}
    trace = _trace(root, str(replay.trace_job_id))
    assert trace["status"] == "replayed"
    assert [step["stage"] for step in trace["steps"]] == ["validate", "runtime", "fingerprint", "replay", "drawing"]
    assert [entry["status"] for entry in read_history(root / ".trace")] == ["accepted", "replayed"]


def test_worker_failure_is_traced_without_masking_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_engine(monkeypatch)
    monkeypatch.setattr(jobs, "run_freecad_worker", lambda *_args: WorkerResult(1, 0.01, 0, "worker_timeout", None))
    source = tmp_path / "part.json"
    source.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    root = tmp_path / "out"

    result = jobs.build_cad_job(source, root, tmp_path / "FreeCADCmd.exe", 30)

    assert result.status == "rejected" and result.error_code == "worker_timeout"
    assert result.trace_job_id is not None
    trace = _trace(root, result.trace_job_id)
    assert trace["status"] == "failed"
    worker = next(step for step in trace["steps"] if step["stage"] == "worker")
    assert worker["status"] == "failed" and worker["note"] == "CadProductionError"
    assert trace["steps"][-2]["stage"] == "outcome" and trace["steps"][-2]["note"] == "worker_timeout"
    assert [item["file"] for item in trace["artifacts"]] == ["ir.json", "drawing.svg", "drawing.dxf"]
    [entry] = read_history(root / ".trace")
    assert entry["status"] == "failed"
    # A failed run still tells a person how to build the part by hand.
    assert (root / ".trace" / "jobs" / result.trace_job_id / "MANUAL-STEPS.md").is_file()
    assert not list(root.glob("[0-9a-f]" * 8 + "*"))


def test_runtime_unavailable_is_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from book_production_platform.cad_production.errors import CadProductionError

    def unavailable(_path: Path) -> None:
        raise CadProductionError("freecad_unavailable")

    monkeypatch.setattr(jobs, "inspect_freecad_runtime", unavailable)
    source = tmp_path / "part.json"
    source.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    result = jobs.build_cad_job(source, tmp_path / "out", tmp_path / "FreeCADCmd.exe", 30)

    assert result.error_code == "freecad_unavailable"
    trace = _trace(tmp_path / "out", str(result.trace_job_id))
    assert trace["status"] == "failed"
    assert next(step for step in trace["steps"] if step["stage"] == "runtime")["status"] == "failed"


def test_invalid_part_ir_writes_no_trace(tmp_path: Path) -> None:
    source = tmp_path / "part.json"
    source.write_text("{bad", encoding="utf-8")
    result = jobs.build_cad_job(source, tmp_path / "out", tmp_path / "FreeCADCmd.exe", 30)

    assert result.error_code == "invalid_part_ir" and result.trace_job_id is None
    assert not (tmp_path / "out").exists()


def test_trace_write_failure_never_changes_the_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(jobs, "write_drawings", broken)
    result, _, _ = _run(tmp_path, monkeypatch)

    assert result.status == "accepted" and result.error_code is None
    assert result.trace_job_id is None


def test_trace_and_history_hold_no_private_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, root, _ = _run(tmp_path, monkeypatch, input_dir=CANARY)
    install_fake_engine(monkeypatch)

    trace_text = (root / ".trace" / "jobs" / str(result.trace_job_id) / "trace.json").read_text(encoding="utf-8")
    history_text = (root / ".trace" / "history.jsonl").read_text(encoding="utf-8")
    for text in (trace_text, history_text):
        assert CANARY not in text
        assert str(tmp_path) not in text


def test_matching_ai_proposal_sidecar_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_engine(monkeypatch)
    source = tmp_path / "part.json"
    source.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    fake = SimpleNamespace(
        part_ir=BASE_PART_IR, adapter="provider-gateway", adapter_version="cad-brief-adapter.v1",
        model_calls=1, provided=["width_mm", "height_mm"],
    )
    _write_proposal_sidecar(source, fake, mode="live")

    result = jobs.build_cad_job(source, tmp_path / "out", tmp_path / "FreeCADCmd.exe", 30)
    trace = _trace(tmp_path / "out", str(result.trace_job_id))
    assert trace["ai"]["used"] is True and trace["ai"]["model_calls"] == 1
    assert trace["ai"]["adapter"] == "provider-gateway/cad-brief-adapter.v1"
    assert read_history(tmp_path / "out" / ".trace")[0]["ai_used"] is True

    edited = json.loads(json.dumps(BASE_PART_IR))
    edited["parameters"]["thickness_mm"] = "9.000"
    source.write_text(json.dumps(edited), encoding="utf-8")
    second = jobs.build_cad_job(source, tmp_path / "out", tmp_path / "FreeCADCmd.exe", 30)
    trace = _trace(tmp_path / "out", str(second.trace_job_id))
    assert trace["ai"]["used"] is False
    assert any(step["operation"] == "proposal_edited_by_human" for step in trace["steps"])


def test_guide_carries_every_dimension_constraint_and_hole_exactly() -> None:
    part = _part()
    guide = build_freecad_guide(part)
    text = render_manual_markdown(guide)
    values = [step.value for step in guide.steps]
    labels = dimension_labels(part)

    assert guide.program == "FreeCAD" and guide.program_version.startswith("1.0")
    assert f"{labels['width_mm']} mm" in values and f"{labels['height_mm']} mm" in values
    assert values.count(f"{labels['thickness_mm']} mm") == 2  # Pad length + HoleSketch Z offset
    assert values.count(f"{labels['hole_diameter_mm']} mm") == 4
    for hole in plate_properties(part).hole_centers:
        assert f"{mm(hole.x_mm)} mm" in values and f"{mm(hole.y_mm)} mm" in values
        assert f"X = {mm(hole.x_mm)}, Y = {mm(hole.y_mm)}" in text
    for tool in (
        "Part Design ▸ Create body",
        "Part Design ▸ Create sketch",
        "Sketch ▸ Sketcher geometries ▸ Create rectangle",
        "Part Design ▸ Create an additive feature ▸ Pad",
        "Part Design ▸ Create a subtractive feature ▸ Pocket",
        "Sketch ▸ Sketcher constraints ▸ Constrain diameter",
    ):
        assert any(step.tool == tool for step in guide.steps), tool
    assert any(step.value == "Through all" for step in guide.steps)
    assert sum(1 for step in guide.steps if "Constrain" in step.tool) == 4 + 12
    assert not any("Fillet" in step.tool or "Chamfer" in step.tool for step in guide.steps)
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0
    assert "83774.536" in text  # analytical volume check


def test_guide_includes_measured_qa_when_available() -> None:
    from tests.cad_production.test_jobs import fake_qa

    text = render_manual_markdown(build_freecad_guide(_part(), qa=fake_qa()))
    assert "83774.535" in text and "bores 4" in text


def test_properties_match_closed_form_geometry() -> None:
    props = plate_properties(_part())
    expected = (137 * 83 - 4 * math.pi * 16) * 7.5
    assert props.volume_mm3 == pytest.approx(expected)
    assert [(h.x_mm, h.y_mm) for h in props.hole_centers] == [
        (Decimal("13.000"), Decimal("17.000")),
        (Decimal("124.000"), Decimal("17.000")),
        (Decimal("124.000"), Decimal("66.000")),
        (Decimal("13.000"), Decimal("66.000")),
    ]
    assert props.edge_web_x_mm == Decimal("9.000") and props.inner_web_y_mm == Decimal("41.000")
    assert props.mass_g("aluminium-6061") == pytest.approx(expected * 2.70 / 1000)


def test_drawings_are_deterministic_and_carry_every_dimension() -> None:
    part = _part()
    svg, dxf = render_svg(part), render_dxf(part)
    assert svg == render_svg(part) and dxf == render_dxf(part)
    svg_text = svg.decode("utf-8")
    assert 'width="' in svg_text and 'mm"' in svg_text
    for label in dimension_labels(part).values():
        assert f">{label}<" in svg_text or f"Ø{label}" in svg_text
    assert "4x Ø8.000 THRU ALL" in svg_text

    lines = dxf.decode("ascii").splitlines()
    pairs = list(zip(lines[0::2], lines[1::2], strict=True))
    assert pairs[0] == ("0", "SECTION") and pairs[-1] == ("0", "EOF")
    circles = []
    for index, (code, value) in enumerate(pairs):
        if (code, value) == ("0", "CIRCLE"):
            entity = dict(pairs[index + 1 : index + 6])
            circles.append((entity["8"], entity["10"], entity["20"], entity["40"]))
    assert circles == [
        ("HOLES", "13.000", "17.000", "4.000"),
        ("HOLES", "124.000", "17.000", "4.000"),
        ("HOLES", "124.000", "66.000", "4.000"),
        ("HOLES", "13.000", "66.000", "4.000"),
    ]
    texts = {value for code, value in pairs if code == "1"}
    for name in ("width_mm", "height_mm", "thickness_mm", "hole_offset_x_mm", "hole_offset_y_mm"):
        assert dimension_labels(part)[name] in texts
    assert "4x %%c8.000 THRU ALL" in texts
