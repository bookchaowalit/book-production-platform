from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest
from data_ir import ChartSpec
from data_production.aggregation import build_chart_ir
from data_production.qa import qa_outputs
from data_production.recipe import build_dataset_ir
from data_production.render import nice_axis, render_outputs


def test_render_outputs_reopen_with_escaped_labels_and_report(tmp_path: Path) -> None:
    dataset = build_dataset_ir({"dataset_id": "safe", "csv_text": "kind,amount\n<A>,2\nB,3\n"})
    chart = build_chart_ir(
        dataset,
        ChartSpec(
            x_column="kind",
            y_column="amount",
            aggregation="sum",
            title="<Sales>",
            x_axis_label="Region & segment",
            y_axis_label="<Net sales>",
            value_unit="THB",
        ),
    )
    artifacts = render_outputs(dataset, chart, tmp_path)
    assert {artifact.filename for artifact in artifacts} == {"cleaned.csv", "chart.svg", "report.json"}
    svg = (tmp_path / "chart.svg").read_text(encoding="utf-8")
    assert "&lt;A&gt;" in svg
    assert "&lt;Sales&gt;" in svg
    assert "Region &amp; segment" in svg
    assert "&lt;Net sales&gt; (THB)" in svg
    root = ElementTree.fromstring(svg)
    assert len(root.findall(".//*[@class='y-tick']")) == 5
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["chart"]["x_axis_label"] == "Region & segment"
    assert report["chart"]["y_axis_label"] == "<Net sales>"
    assert report["chart"]["value_unit"] == "THB"
    assert qa_outputs(dataset, chart, tmp_path) == ()


def test_qa_rejects_tampered_report(tmp_path: Path) -> None:
    dataset = build_dataset_ir({"dataset_id": "tamper", "csv_text": "kind\nA\n"})
    chart = build_chart_ir(dataset, ChartSpec(x_column="kind", aggregation="count"))
    render_outputs(dataset, chart, tmp_path)
    report = (tmp_path / "report.json").read_text(encoding="utf-8").replace('"aggregate_total": "1"', '"aggregate_total": "9"')
    (tmp_path / "report.json").write_text(report, encoding="utf-8")
    assert "report_total_mismatch" in qa_outputs(dataset, chart, tmp_path)


def test_qa_rejects_tampered_axis_labels_and_unit(tmp_path: Path) -> None:
    dataset = build_dataset_ir({"dataset_id": "axes", "csv_text": "kind,amount\nA,3\n"})
    chart = build_chart_ir(
        dataset,
        ChartSpec(
            x_column="kind",
            y_column="amount",
            aggregation="sum",
            x_axis_label="Region",
            y_axis_label="Revenue",
            value_unit="THB",
        ),
    )
    render_outputs(dataset, chart, tmp_path)

    svg_path = tmp_path / "chart.svg"
    svg_path.write_text(svg_path.read_text(encoding="utf-8").replace("Region", "Area", 1), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["chart"]["value_unit"] = "USD"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    issues = qa_outputs(dataset, chart, tmp_path)
    assert "chart_x_axis_label_mismatch" in issues
    assert "report_value_unit_mismatch" in issues


def test_axis_ticks_include_negative_zero_and_positive_values(tmp_path: Path) -> None:
    dataset = build_dataset_ir(
        {
            "dataset_id": "signed-axis",
            "csv_text": "kind,amount\nLoss,-5\nBreak-even,0\nGain,5\n",
        }
    )
    chart = build_chart_ir(
        dataset,
        ChartSpec(x_column="kind", y_column="amount", aggregation="sum", value_unit="points"),
    )

    render_outputs(dataset, chart, tmp_path)

    root = ElementTree.parse(tmp_path / "chart.svg").getroot()
    tick_labels = [node.text for node in root.findall(".//*[@class='y-tick-label']")]
    assert tick_labels == ["-5", "-2.5", "0", "2.5", "5"]
    assert "(points)" in "".join(root.itertext())


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        ("0", "2000.5", ("0", "2400")),
        ("-5", "5", ("-5", "5")),
        ("0", "0.37", ("0", "0.4")),
        ("-120", "30", ("-120", "40")),
        ("0", "83774.5", ("0", "100000")),
    ],
)
def test_nice_axis_uses_round_steps_and_covers_values(low: str, high: str, expected: tuple[str, str]) -> None:
    axis_low, axis_high = nice_axis(Decimal(low), Decimal(high))
    assert (axis_low, axis_high) == (Decimal(expected[0]), Decimal(expected[1]))
    step = (axis_high - axis_low) / 4
    assert axis_low <= Decimal(low) and axis_high >= Decimal(high)
    leading = (step / Decimal(10) ** step.adjusted()).normalize()
    assert leading in {Decimal(value) for value in ("1", "1.5", "2", "2.5", "3", "4", "5", "6", "8")}
