from pathlib import Path

from test_recipe import request

from book_production_platform.spreadsheet_production.qa import qa_workbook
from book_production_platform.spreadsheet_production.recipe import build_workbook_ir
from book_production_platform.spreadsheet_production.render import render_workbook


def test_qa_workbook_accepts_reopenable_artifact(tmp_path: Path) -> None:
    ir = build_workbook_ir(request())
    output = tmp_path / "qa.xlsx"
    render_workbook(ir, output)

    assert qa_workbook(ir, output) == ()


def test_qa_workbook_rejects_truncated_artifact(tmp_path: Path) -> None:
    ir = build_workbook_ir(request())
    output = tmp_path / "broken.xlsx"
    render_workbook(ir, output)
    output.write_bytes(output.read_bytes()[:64])

    assert "xlsx_unreadable" in qa_workbook(ir, output)
