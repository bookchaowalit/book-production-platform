from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.spreadsheet_production.jobs import run_workbook_job
from book_production_platform.spreadsheet_production.recipe import (
    WorkbookBuildError,
    build_workbook_ir,
)


def _table(name: str, amounts: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "source_ref": f"test:{name}",
        "columns": [
            {"key": "item", "label": "รายการ", "data_type": "text", "required": True},
            {"key": "amount", "label": "จำนวน", "data_type": "decimal", "required": True},
        ],
        "rows": [{"item": f"{name}-{index}", "amount": amount} for index, amount in enumerate(amounts)],
    }


def _request(*tables: dict[str, Any]) -> dict[str, Any]:
    return {"workbook_id": "multi-1", "title": "หลายตาราง", "locale": "th-TH", "tables": list(tables)}


def test_every_table_gets_its_own_sheet_and_summary_rows() -> None:
    ir = build_workbook_ir(_request(_table("Aggregates", ["4.5", "17.5"]), _table("Elements", ["1", "2", "3"])))
    assert [sheet.name for sheet in ir.sheets] == ["Summary", "Detail", "Elements"]
    labels = [cell.value for cell in ir.sheets[0].cells if cell.address.startswith("Summary!A") and cell.address != "Summary!A1"]
    assert "Aggregates: จำนวน" in labels
    assert "Elements: จำนวน" in labels
    assert ir.named_ranges["Elements_amount"] == "Elements!B2:B4"
    assert ir.assumptions["source_tables"] == "Aggregates,Elements"


def test_single_table_keeps_historical_layout() -> None:
    ir = build_workbook_ir(_request(_table("Orders", ["1250.50", "800.00"])))
    assert [sheet.name for sheet in ir.sheets] == ["Summary", "Detail"]
    assert ir.named_ranges == {"Detail_amount": "Detail!B2:B3"}
    labels = [cell.value for cell in ir.sheets[0].cells if cell.address == "Summary!A4"]
    assert labels == ["จำนวน"]


@pytest.mark.parametrize("second", ["Summary", "Detail", "detail", "A" * 32])
def test_conflicting_sheet_names_are_rejected(second: str) -> None:
    with pytest.raises(WorkbookBuildError) as caught:
        build_workbook_ir(_request(_table("First", ["1"]), _table(second, ["2"])))
    assert caught.value.issues[0].code == "sheet_name_conflict"


def test_multi_table_workbook_renders_all_sheets(tmp_path: Path) -> None:
    result = run_workbook_job(_request(_table("Aggregates", ["4.5"]), _table("Elements", ["1", "2"])), tmp_path, "multi")
    assert result.status == "accepted", result
    assert result.manifest_path is not None
    with zipfile.ZipFile(result.manifest_path.parent / "workbook.xlsx") as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        shared = archive.read("xl/sharedStrings.xml").decode("utf-8")
    assert re.findall(r'<sheet name="([^"]+)"', workbook_xml) == ["Summary", "Detail", "Elements"]
    assert "Elements-1" in shared
