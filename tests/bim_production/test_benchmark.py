from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_production_platform.bim_production.benchmark import (
    BENCHMARK_PROTOCOL,
    TOTAL_CASES,
    load_benchmark_cases,
    run_benchmark,
)
from tests.bim_production.fixtures import require_pinned_runtime

CASES_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "bim" / "cases.json"
FIXTURE_MACRO = Path(__file__).resolve().parents[1] / "bim_production" / "fixture_worker.FCMacro"


def test_pinned_registry_has_exactly_twenty_cases() -> None:
    registry = load_benchmark_cases(CASES_PATH)

    assert registry["protocol"] == BENCHMARK_PROTOCOL
    assert len(registry["fresh"]) == 5
    complete = sum(1 for case in registry["fresh"] if case["expected"] == "complete")
    partial = sum(1 for case in registry["fresh"] if case["expected"] == "partial")
    assert (complete, partial) == (4, 1)
    assert len(registry["rejects"]) == 8
    codes = {case["expected_code"] for case in registry["rejects"]}
    assert codes <= {
        "invalid_bim_takeoff_ir",
        "ifc_path_invalid",
        "ifc_parse_invalid",
        "unsupported_ifc_schema",
        "ifc_identity_invalid",
        "ifc_schema_invalid",
    }


def test_registry_rejects_a_broken_composition(tmp_path: Path) -> None:
    registry = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    registry["fresh"] = registry["fresh"][:3]
    broken = tmp_path / "cases.json"
    broken.write_text(json.dumps(registry), encoding="utf-8")

    from book_production_platform.bim_production.errors import BimProductionError

    with pytest.raises(BimProductionError) as error:
        load_benchmark_cases(broken)

    assert error.value.code == "invalid_bim_takeoff_ir"


@pytest.fixture(scope="module")
def runtime() -> tuple[Path, Path]:
    return require_pinned_runtime()


def test_full_benchmark_passes_twenty_cases(
    runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    freecadcmd, site_packages = runtime
    output_root = tmp_path / "benchmark-output"
    benchmark_output = tmp_path / "benchmarks"

    summary = run_benchmark(
        CASES_PATH,
        output_root,
        freecadcmd,
        site_packages,
        FIXTURE_MACRO,
        timeout_s=120,
    )

    benchmark_output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n"
    (benchmark_output / "latest.json").write_text(payload, encoding="utf-8")

    assert summary["status"] == "passed"
    assert summary["passed"] == TOTAL_CASES
    assert summary["total"] == TOTAL_CASES
    assert summary["model_calls_total"] == 0
    assert summary["monetary_cost"] == "unknown"
    outcomes = {case["id"]: case["outcome"] for case in summary["cases"]}
    assert len(outcomes) == TOTAL_CASES
    replayed = [name for name, outcome in outcomes.items() if name.startswith("replay-")]
    assert len(replayed) == 5
    assert all(outcomes[name] == "replayed" for name in replayed)
    rejected = [name for name, outcome in outcomes.items() if name.startswith("reject-")]
    assert len(rejected) == 8
    assert all(outcomes[name] == "rejected" for name in rejected)
    tampered = [name for name, outcome in outcomes.items() if name.startswith("tamper-")]
    assert len(tampered) == 2
    assert all(outcomes[name] == "rejected" for name in tampered)
    serialized = payload
    assert "C:\\" not in serialized and "/home/" not in serialized
    assert str(tmp_path) not in serialized
