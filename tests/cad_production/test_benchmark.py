from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from book_production_platform.cad_ir import PartIRError, canonical_part_ir, load_part_ir
from book_production_platform.cad_production import benchmark
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.jobs import ARTIFACT_NAMES, CadJobResult
from book_production_platform.cad_production.qa import CadQAResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"


def _qa() -> CadQAResult:
    return CadQAResult(
        bounds_mm=(0.0, 137.0, 0.0, 83.0, 0.0, 7.5),
        volume_mm3=83774.5355,
        solid_count=1,
        bore_count=4,
        step_relative_volume_error=0.0,
        stl_triangle_count=500,
        stl_volume_mm3=83774.5,
        tessellation_vertices=252,
        tessellation_faces=500,
        preview_profile="isometric-1024x768.v1",
    )


def _fake_job(input_path: Path, output_root: Path, _freecadcmd: Path, _timeout_s: int) -> CadJobResult:
    try:
        part = load_part_ir(input_path)
    except PartIRError as error:
        return CadJobResult(None, "rejected", None, {}, None, False, 0, 0, error.code)

    fingerprint = hashlib.sha256(canonical_part_ir(part)).hexdigest()
    artifact_dir = output_root / fingerprint
    receipt = output_root / ".receipts" / f"{fingerprint}.json"
    expected = {
        name: f"{fingerprint}:{name}".encode("ascii") for name in ARTIFACT_NAMES
    }
    if artifact_dir.exists() or receipt.exists():
        if (
            not artifact_dir.is_dir()
            or {entry.name for entry in artifact_dir.iterdir()} != ARTIFACT_NAMES
            or not receipt.is_file()
            or any((artifact_dir / name).read_bytes() != content for name, content in expected.items())
            or receipt.read_bytes() != fingerprint.encode("ascii")
        ):
            return CadJobResult(
                fingerprint, "rejected", None, {}, None, False, 0, 0, "replay_integrity_error"
            )
        return CadJobResult(
            fingerprint,
            "replayed",
            artifact_dir,
            {name: hashlib.sha256(content).hexdigest() for name, content in expected.items()},
            _qa(),
            True,
            0,
            0,
            None,
        )

    artifact_dir.mkdir(parents=True)
    for name, content in expected.items():
        (artifact_dir / name).write_bytes(content)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(fingerprint.encode("ascii"))
    return CadJobResult(
        fingerprint,
        "accepted",
        artifact_dir,
        {name: hashlib.sha256(content).hexdigest() for name, content in expected.items()},
        _qa(),
        False,
        sum(len(content) for content in expected.values()) + len(fingerprint),
        0,
        None,
    )


def test_benchmark_runs_twenty_cases_with_safe_aggregate_report(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(benchmark, "build_cad_job", _fake_job)
    output = tmp_path / "acceptance.json"

    report = benchmark.run_benchmark(FIXTURES, output, Path("FreeCADCmd.exe"))

    assert report["status"] == "passed"
    assert report["cases_total"] == 20
    assert report["summary"] == {
        "fresh_accepted": 5,
        "replayed": 5,
        "input_rejected": 8,
        "tamper_rejected": 2,
        "qa_failures": 0,
        "unexpected": 0,
    }
    assert report["model_calls"] == 0
    assert report["monetary_cost"] == "unknown"
    assert len(report["cases"]) == 20
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert '"width_mm"' not in serialized
    assert '"height_mm"' not in serialized
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8")) == report
    jobs_root = output.with_name(f"{output.stem}.jobs")
    assert len([path for path in jobs_root.iterdir() if len(path.name) == 64]) == 5


def test_fixture_registry_has_exact_expected_case_counts() -> None:
    cases = benchmark._load_cases(FIXTURES)
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["kind"]] = counts.get(case["kind"], 0) + 1
    assert counts == {"accept": 5, "replay": 5, "reject": 8, "tamper_replay": 2}


def test_benchmark_refuses_existing_report_or_jobs_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(benchmark, "build_cad_job", _fake_job)
    output = tmp_path / "acceptance.json"
    output.write_text("already here", encoding="utf-8")

    with pytest.raises(CadProductionError) as error:
        benchmark.run_benchmark(FIXTURES, output, Path("FreeCADCmd.exe"))
    assert error.value.code == "benchmark_output_exists"
