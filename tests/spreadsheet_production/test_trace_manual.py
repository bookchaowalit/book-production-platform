"""Spreadsheet job history, trace, manual Excel guide, and the parity features (formats, dropdowns, highlights)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

import book_production_platform.spreadsheet_production.jobs as jobs_module
from book_production_platform.production_trace import (
    read_history,
    render_manual_markdown,
    sha256_file,
    sha256_json,
    verify_trace,
)
from book_production_platform.spreadsheet_ir import WorkbookRequest, validate_request
from book_production_platform.spreadsheet_production.formulas import evaluate_workbook
from book_production_platform.spreadsheet_production.jobs import (
    RECIPE_VERSION,
    run_workbook_job,
)
from book_production_platform.spreadsheet_production.manual import (
    build_excel_guide,
    display_value,
)
from book_production_platform.spreadsheet_production.provider import propose_structured
from book_production_platform.spreadsheet_production.qa import qa_workbook
from book_production_platform.spreadsheet_production.recipe import build_workbook_ir
from book_production_platform.spreadsheet_production.render import (
    absolute_reference,
    render_workbook,
)

CANARY = "CANARYXLSTRACE7731"


def rich_request() -> dict[str, Any]:
    return {
        "workbook_id": "trace-001",
        "title": "Trace workbook",
        "locale": "en-US",
        "tables": [
            {
                "name": "Orders",
                "source_ref": "fixture:orders",
                "columns": [
                    {"key": "item", "label": "Item", "data_type": "text", "required": True},
                    {"key": "amount", "label": "Amount", "data_type": "decimal", "required": True, "number_format": '"฿"#,##0.00'},
                    {"key": "qty", "label": "Qty", "data_type": "integer", "number_format": "#,##0"},
                    {"key": "sold_on", "label": "Sold on", "data_type": "date", "number_format": "dd/mm/yyyy"},
                    {"key": "status", "label": "Status", "data_type": "text", "allowed_values": ["open", "closed", "void"]},
                ],
                "rows": [
                    {"item": f"secret item {CANARY}", "amount": "1250.50", "qty": 3, "sold_on": "2026-09-22", "status": "open"},
                    {"item": "=SUM(A1:A2)", "amount": "99.25", "qty": 12, "sold_on": "2026-09-23", "status": "closed"},
                    {"item": "00123", "amount": "10", "qty": None, "sold_on": None, "status": None},
                ],
            },
            {
                "name": "Costs",
                "source_ref": "fixture:costs",
                "columns": [{"key": "cost", "label": "Cost", "data_type": "decimal"}],
                "rows": [{"cost": "7.5"}, {"cost": "2.5"}],
            },
        ],
        "formulas": [{"target": "Summary!D4", "expression": "=IF(Summary!B4>1000,\"high\",\"low\")", "result_type": "text"}],
        "conditional_formats": [
            {"table": "Orders", "column": "amount", "operator": "greater_than", "value": "1000"},
            {"table": "Costs", "column": "cost", "operator": "between", "value": "2", "value2": "5", "fill_color": "#c6efce"},
        ],
    }


def _trace(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "trace.json").read_text(encoding="utf-8"))


def test_accepted_job_records_trace_ir_manual_and_history(tmp_path: Path) -> None:
    result = run_workbook_job(rich_request(), tmp_path, f"key-{CANARY}")

    assert result.status == "accepted" and result.manifest_path is not None
    job_dir = result.manifest_path.parent
    trace = _trace(job_dir)
    assert trace["tool_id"] == "spreadsheet-v1a" and trace["status"] == "accepted"
    assert trace["recipe_version"] == RECIPE_VERSION
    assert trace["inputs"][0]["rows"] == [3, 2] and trace["inputs"][0]["columns"] == [5, 1]
    assert [step["operation"] for step in trace["steps"]][:4] == ["parse_request", "build_workbook_ir", "evaluate_formulas", "render_xlsx"]
    assert [step["params"]["sheet"] for step in trace["steps"] if step["operation"] == "write_sheet"] == ["Summary", "Detail", "Costs"]
    assert trace["artifacts"] == [{
        "file": "workbook.xlsx", "role": "output", "media_type": result.artifact.media_type if result.artifact else "",
        "bytes": (job_dir / "workbook.xlsx").stat().st_size, "sha256": sha256_file(job_dir / "workbook.xlsx"),
    }]
    ir_payload = json.loads((job_dir / "ir.json").read_text(encoding="utf-8"))
    assert trace["ir"] == {"schema": "workbook-ir.v1", "sha256": sha256_json(ir_payload), "file": "ir.json"}
    assert trace["manual_guide"]["program"] == "Microsoft Excel"
    assert (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8").startswith("# วิธีทำเองด้วย Microsoft Excel")
    history = read_history(tmp_path)
    assert [(item["job_id"], item["status"]) for item in history] == [(result.job_id, "accepted")]
    assert history[0]["trace"] == f"{job_dir.name}/trace.json"


def test_trace_and_history_hold_no_cell_values_or_raw_key(tmp_path: Path) -> None:
    result = run_workbook_job(rich_request(), tmp_path, f"key-{CANARY}")
    assert result.manifest_path is not None
    job_dir = result.manifest_path.parent

    for text in ((job_dir / "trace.json").read_text(encoding="utf-8"), (tmp_path / "history.jsonl").read_text(encoding="utf-8")):
        assert CANARY not in text
        assert "1250.5" not in text and "=SUM(A1:A2)" not in text and "Trace workbook" not in text
    # The manual guide is the human recipe: it carries the content to type.
    assert CANARY in (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")


def test_replay_adds_no_history_and_verify_detects_tamper(tmp_path: Path) -> None:
    first = run_workbook_job(rich_request(), tmp_path, "key-replay")
    second = run_workbook_job(rich_request(), tmp_path, "key-replay")

    assert second.replayed is True
    assert len(read_history(tmp_path)) == 1
    assert verify_trace(tmp_path, first.job_id)["ok"] is True
    assert first.manifest_path is not None
    with (first.manifest_path.parent / "workbook.xlsx").open("ab") as handle:
        handle.write(b"tampered")
    report = verify_trace(tmp_path, first.job_id)
    assert report["ok"] is False and report["artifacts"][0]["ok"] is False


def test_rejected_jobs_are_traced_without_masking_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = run_workbook_job({**rich_request(), "tables": []}, tmp_path, "key-invalid")
    bad_value = copy.deepcopy(rich_request())
    bad_value["tables"][0]["rows"][0]["status"] = "lost"
    not_listed = run_workbook_job(bad_value, tmp_path, "key-not-listed")
    monkeypatch.setattr(jobs_module, "qa_workbook", lambda *args: ("formula_mismatch:Summary!B4",))
    qa_failed = run_workbook_job(rich_request(), tmp_path, "key-qa")

    assert (invalid.status, invalid.error_code) == ("rejected", "invalid_request")
    assert (not_listed.status, not_listed.error_code) == ("rejected", "invalid_request")
    assert (qa_failed.status, qa_failed.error_code) == ("rejected", "qa_failed")
    history = read_history(tmp_path)
    assert [item["status"] for item in history] == ["failed", "failed", "failed"]
    for item in history:
        trace = json.loads((tmp_path / item["trace"]).read_text(encoding="utf-8"))
        assert trace["steps"][-1]["operation"] == "failed" and trace["artifacts"] == []
        assert not (tmp_path / item["trace"]).with_name("MANUAL-STEPS.md").exists()
    notes = [json.loads((tmp_path / item["trace"]).read_text(encoding="utf-8"))["steps"][-1]["note"] for item in history]
    assert notes == ["invalid_request", "invalid_request", "qa_failed"]


def test_ai_proposal_metadata_is_recorded(tmp_path: Path) -> None:
    metadata = {"status": "complete", "model_calls": 2, "adapter": "spreadsheet-provider-gateway", "mode": "live", "issue_codes": []}
    result = run_workbook_job(rich_request(), tmp_path, "key-ai", ai_proposal=metadata)

    assert result.model_calls == 2 and result.manifest_path is not None
    ai = _trace(result.manifest_path.parent)["ai"]
    assert ai["used"] is True and ai["model_calls"] == 2 and ai["adapter"] == "spreadsheet-provider-gateway"
    assert read_history(tmp_path)[0]["ai_used"] is True


def _guide_text(request: dict[str, Any]) -> tuple[Any, str]:
    ir = build_workbook_ir(request)
    return ir, render_manual_markdown(build_excel_guide(ir))


def test_guide_lists_every_sheet_formula_range_format_and_rule_from_the_ir() -> None:
    ir, text = _guide_text(rich_request())
    guide = build_excel_guide(ir)
    values = evaluate_workbook(ir).values

    assert [layer.name for layer in guide.layers if layer.kind == "Worksheet"] == [sheet.name for sheet in ir.sheets]
    rename_steps = [step.value for step in guide.steps if "Rename" in step.tool]
    assert rename_steps == [sheet.name for sheet in ir.sheets]
    formula_steps = [(step.target, step.value) for step in guide.steps if step.tool == "Formula Bar"]
    assert formula_steps == [(formula.target, formula.expression) for formula in ir.formulas]
    for formula in ir.formulas:
        assert f"`{formula.expression}`" in text
        assert f"{formula.target} แสดงค่า {display_value(values[formula.target])}" in text
    for name, reference in ir.named_ranges.items():
        assert any(step.target == name and step.value == absolute_reference(reference) for step in guide.steps)
    for sheet in ir.sheets:
        for item in sheet.column_formats:
            assert any(step.target == f"{sheet.name}!{item.range}" and step.value == item.number_format for step in guide.steps)
        for validation in sheet.data_validations:
            assert any(step.target == f"{sheet.name}!{validation.range}" and step.value == ",".join(validation.allowed_values) for step in guide.steps)
        for rule in sheet.conditional_formats:
            assert any(step.target == f"{sheet.name}!{rule.range}" and step.value == rule.fill_color for step in guide.steps)
    assert '`"฿"#,##0.00`' in text and "`dd/mm/yyyy`" in text and "`open,closed,void`" in text
    assert "Cell Value greater than 1000" in text and "Cell Value between 2 and 5" in text
    # Literal text that Excel would reinterpret is typed with a leading apostrophe.
    assert "A3='=SUM(A1:A2)" in text and "A4='00123" in text
    assert guide.estimated_minutes is not None and guide.estimated_minutes > 0


def test_guide_covers_v1b_intent_formulas_exactly() -> None:
    dataset = {
        "name": "sales",
        "header": ["Region", "Amount", "Order Date"],
        "rows": [
            {"Region": "North", "Amount": "10.5", "Order Date": "2026-01-02"},
            {"Region": "South", "Amount": "4", "Order Date": "2026-01-03"},
            {"Region": "North", "Amount": "1.5", "Order Date": "2026-01-04"},
        ],
    }
    proposal = {
        "template_id": "sales-summary",
        "mapping": {"Region": "category", "Amount": "amount", "Order Date": "date"},
        "intents": [
            {"intent": "sum", "column": "amount"},
            {"intent": "average", "column": "amount"},
            {"intent": "group-by", "group_column": "category", "aggregate": "sum", "column": "amount"},
            {"intent": "lookup", "key_column": "category", "key": "South", "column": "amount"},
        ],
    }
    result = propose_structured({"workbook_id": "intents", "dataset": dataset, "proposal": proposal})
    assert result.status == "complete" and result.request is not None
    ir, text = _guide_text(result.request)
    guide = build_excel_guide(ir)

    expressions = [formula.expression for formula in ir.formulas]
    assert any(item.startswith("=SUMIF(") for item in expressions) and any(item.startswith("=INDEX(") for item in expressions)
    assert [(step.target, step.value) for step in guide.steps if step.tool == "Formula Bar"] == [(f.target, f.expression) for f in ir.formulas]
    assert all(f"`{expression}`" in text for expression in expressions)
    assert "Analysis" in [layer.name for layer in guide.layers]


def test_large_sheets_copy_data_rows_instead_of_typing_each_one() -> None:
    request = {
        "workbook_id": "big", "title": "Big",
        "tables": [{"name": "T", "columns": [{"key": "n", "label": "N", "data_type": "integer"}], "rows": [{"n": index} for index in range(250)]}],
    }
    guide = build_excel_guide(build_workbook_ir(request))

    copy_steps = [step for step in guide.steps if "Paste Special" in step.tool]
    assert len(copy_steps) == 1 and copy_steps[0].value == "A2:A251"


def test_rendered_xlsx_has_formats_dropdowns_highlights_and_header_style(tmp_path: Path) -> None:
    ir = build_workbook_ir(rich_request())
    path = tmp_path / "workbook.xlsx"
    render_workbook(ir, path)

    assert qa_workbook(ir, path) == ()
    with zipfile.ZipFile(path) as archive:
        detail = archive.read("xl/worksheets/sheet2.xml").decode()
        costs = archive.read("xl/worksheets/sheet3.xml").decode()
        styles = archive.read("xl/styles.xml").decode()
    assert re.search(r'<dataValidation type="list"[^>]*sqref="E2:E4"', detail)
    assert "<formula1>\"open,closed,void\"</formula1>" in detail
    assert '<conditionalFormatting sqref="B2:B4">' in detail and "<formula>1000</formula>" in detail
    assert '<conditionalFormatting sqref="A2:A3">' in costs
    for code in ('&quot;฿&quot;#,##0.00', "#,##0", "dd/mm/yyyy"):
        assert f'formatCode="{code}"' in styles
    header_style = re.search(r'<c r="A1" s="(\d+)"', detail)
    body_style = re.search(r'<c r="A2" s="(\d+)"', detail)
    assert header_style and body_style and header_style.group(1) != body_style.group(1)


def test_qa_reports_missing_dropdowns_and_highlights(tmp_path: Path) -> None:
    ir = build_workbook_ir(rich_request())
    path = tmp_path / "workbook.xlsx"
    render_workbook(ir.model_copy(update={"sheets": [sheet.model_copy(update={"data_validations": [], "conditional_formats": []}) for sheet in ir.sheets]}), path)

    issues = qa_workbook(ir, path)
    assert "data_validation_mismatch:Detail" in issues and "conditional_format_mismatch:Detail" in issues


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: r["tables"][0]["columns"][1].update(number_format="0.000"), "number_format_not_allowed"),
        (lambda r: r["tables"][0]["columns"][3].update(number_format="#,##0"), "number_format_not_allowed"),
        (lambda r: r["tables"][0]["columns"][1].update(allowed_values=["1"]), "allowed_values_requires_text"),
        (lambda r: r["tables"][0]["columns"][4].update(allowed_values=["=cmd", "ok"]), "allowed_values_invalid"),
        (lambda r: r["tables"][0]["columns"][4].update(allowed_values=["a,b"]), "allowed_values_invalid"),
        (lambda r: r["conditional_formats"].append({"table": "Orders", "column": "item", "operator": "equal_to", "value": "1"}), "conditional_format_requires_numeric"),
        (lambda r: r["conditional_formats"].append({"table": "Nope", "column": "x", "operator": "equal_to", "value": "1"}), "conditional_format_target_unknown"),
        (lambda r: r["conditional_formats"].append({"table": "Orders", "column": "qty", "operator": "between", "value": "5"}), "conditional_format_value_invalid"),
    ],
)
def test_parity_feature_validation(mutate: Any, code: str) -> None:
    request = copy.deepcopy(rich_request())
    mutate(request)

    assert code in {issue.code for issue in validate_request(request)}


def test_requests_without_new_fields_keep_their_idempotency_hash() -> None:
    plain = {"workbook_id": "w", "title": "T", "tables": [{"name": "T", "columns": [{"key": "a", "label": "A", "data_type": "integer"}], "rows": [{"a": 1}]}]}
    parsed = WorkbookRequest.model_validate(plain)
    legacy = parsed.model_dump(mode="json")
    legacy.pop("conditional_formats")
    for column in legacy["tables"][0]["columns"]:
        column.pop("number_format")
        column.pop("allowed_values")
    expected = hashlib.sha256(
        json.dumps({"recipe": RECIPE_VERSION, "request": legacy}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert jobs_module._canonical_hash(parsed) == expected


def test_named_ranges_beyond_column_z_are_absolute() -> None:
    assert absolute_reference("Detail!AB2:AB10") == "='Detail'!$AB$2:$AB$10"
    columns = [{"key": f"c{index}", "label": f"C{index}", "data_type": "integer"} for index in range(28)]
    request = {"workbook_id": "wide", "title": "Wide", "tables": [{"name": "W", "columns": columns, "rows": [{"c27": 5}]}]}
    ir = build_workbook_ir(request)
    assert ir.named_ranges["Detail_c27"] == "Detail!AB2:AB2"
