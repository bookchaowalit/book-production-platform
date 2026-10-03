"""Deterministic cost mapping over published takeoff aggregates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.bim_production import cli as bim_cli
from book_production_platform.bim_production.cost import (
    build_cost_estimate,
    build_cost_estimate_job,
    parse_rate_table,
    render_estimate_csv,
)
from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.production_trace import read_history, verify_trace

EXAMPLE_RATES = Path(__file__).resolve().parents[2] / "examples" / "bim" / "cost-rates.example.json"


def _report(status: str = "complete") -> dict[str, Any]:
    return {
        "schema_version": "bim-takeoff-report.v1",
        "takeoff_id": "job-demo",
        "status": status,
        "aggregates": [
            {"ifc_class": "IfcWall", "qto_name": "Qto_WallBaseQuantities", "quantity_name": "NetSideArea",
             "measure_kind": "IfcQuantityArea", "unit": "m2", "method": None, "value": "12.345"},
            {"ifc_class": "IfcSlab", "qto_name": "Qto_SlabBaseQuantities", "quantity_name": "NetVolume",
             "measure_kind": "IfcQuantityVolume", "unit": "m3", "method": "net", "value": "3.3335"},
            {"ifc_class": "IfcDoor", "qto_name": "Qto_DoorBaseQuantities", "quantity_name": "Area",
             "measure_kind": "IfcQuantityArea", "unit": "m2", "method": None, "value": "1.89"},
        ],
    }


def _rates(**extra: Any) -> dict[str, Any]:
    rates = [
        {"code": "W-01", "ifc_class": "IfcWall", "qto_name": "Qto_WallBaseQuantities", "quantity_name": "NetSideArea", "unit": "m2", "rate": "450.00"},
        {"code": "S-01", "ifc_class": "IfcSlab", "qto_name": "Qto_SlabBaseQuantities", "quantity_name": "NetVolume", "unit": "m3", "method": "net", "rate": "3200.5"},
        {"code": "C-99", "ifc_class": "IfcColumn", "qto_name": "Qto_ColumnBaseQuantities", "quantity_name": "NetVolume", "unit": "m3", "rate": "4100"},
    ]
    return {"schema_version": "bim-cost-rates.v1", "currency": "THB", "rates": rates, **extra}


def test_estimate_prices_exact_keys_rounds_half_up_and_lists_gaps() -> None:
    estimate = build_cost_estimate(_report(), parse_rate_table(_rates()), report_sha256="a" * 64, rates_sha256="b" * 64)

    assert [line["code"] for line in estimate["lines"]] == ["W-01", "S-01"]
    assert [line["amount"] for line in estimate["lines"]] == ["5555.25", "10668.87"]  # 5555.25, 10668.866675 -> .87
    assert estimate["total"] == "16224.12"
    assert estimate["subtotals"] == [{"ifc_class": "IfcSlab", "amount": "10668.87"}, {"ifc_class": "IfcWall", "amount": "5555.25"}]
    assert estimate["unpriced"][0]["ifc_class"] == "IfcDoor"
    assert estimate["unused_rates"] == ["C-99"]
    assert estimate["status"] == "partial"
    csv_text = render_estimate_csv(estimate).decode("utf-8")
    assert csv_text.splitlines()[1] == "W-01,IfcWall,Qto_WallBaseQuantities,NetSideArea,IfcQuantityArea,m2,,12.345,450.00,5555.25,THB"


def test_method_mismatch_is_unpriced_and_duplicate_match_fails_closed() -> None:
    rates = _rates()
    rates["rates"][1]["method"] = "gross"
    estimate = build_cost_estimate(_report(), parse_rate_table(rates), report_sha256="a" * 64, rates_sha256="b" * 64)
    assert {item["ifc_class"] for item in estimate["unpriced"]} == {"IfcSlab", "IfcDoor"}

    ambiguous = _rates()
    ambiguous["rates"].append({**ambiguous["rates"][1]})
    del ambiguous["rates"][-1]["method"]
    with pytest.raises(BimProductionError) as error:
        build_cost_estimate(_report(), parse_rate_table(ambiguous), report_sha256="a" * 64, rates_sha256="b" * 64)
    assert error.value.code == "cost_rates_ambiguous"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(currency="baht"),
        lambda doc: doc["rates"][0].update(rate="-1"),
        lambda doc: doc["rates"][0].update(rate="1e3"),
        lambda doc: doc["rates"][0].update(unit="ft2"),
        lambda doc: doc["rates"][0].update(extra=True),
        lambda doc: doc["rates"].append(dict(doc["rates"][0])),
        lambda doc: doc.update(rates=[]),
    ],
)
def test_rate_table_is_strict(mutate: Any) -> None:
    document = _rates()
    mutate(document)
    with pytest.raises(BimProductionError) as error:
        parse_rate_table(document)
    assert error.value.code == "invalid_cost_rates"


def test_cost_job_writes_estimate_trace_manual_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "takeoff.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(_rates()), encoding="utf-8")
    output = tmp_path / "cost"

    result = build_cost_estimate_job(report, rates, output)
    assert (result.status, result.total, result.line_count, result.unpriced_count) == ("partial", "16224.12", 2, 1)
    assert result.trace_job_id is not None
    manual = (output / ".trace" / "jobs" / result.trace_job_id / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "=ROUND(G2*H2;2)" in manual and "16224.12" in manual and "IfcDoor" in manual
    assert verify_trace(output / ".trace", result.trace_job_id)["ok"] is True
    (output / "cost-estimate.csv").write_text("tampered", encoding="utf-8")
    assert verify_trace(output / ".trace", result.trace_job_id)["ok"] is False

    exit_code = bim_cli.main(["cost", "--report", str(report), "--rates", str(rates), "--output-dir", str(tmp_path / "cli")])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3 and payload["total"] == "16224.12" and payload["model_calls"] == 0


def test_cost_job_failure_is_traced_and_reraised(tmp_path: Path) -> None:
    report = tmp_path / "takeoff.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps({"schema_version": "bim-cost-rates.v1"}), encoding="utf-8")
    with pytest.raises(BimProductionError) as error:
        build_cost_estimate_job(report, rates, tmp_path / "cost")
    assert error.value.code == "invalid_cost_rates"
    [entry] = read_history(tmp_path / "cost" / ".trace")
    assert entry["status"] == "failed"
    assert not (tmp_path / "cost" / "cost-estimate.json").exists()


def test_example_rate_table_is_valid() -> None:
    table = parse_rate_table(json.loads(EXAMPLE_RATES.read_text(encoding="utf-8")))
    assert table.currency == "THB" and len(table.rates) >= 5
