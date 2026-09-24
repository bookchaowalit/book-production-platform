from pathlib import Path
from zipfile import ZipFile

from test_recipe import request

from book_production_platform.spreadsheet_production.recipe import build_workbook_ir
from book_production_platform.spreadsheet_production.render import render_workbook


def test_render_workbook_writes_editable_xlsx_with_summary_and_detail(tmp_path: Path) -> None:
    ir = build_workbook_ir(request())
    output = tmp_path / "sales.xlsx"

    artifacts = render_workbook(ir, output)

    assert output.exists()
    assert artifacts[0].media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    with ZipFile(output) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        assert "Summary" in workbook_xml and "Detail" in workbook_xml
        assert b"<f>SUM(Detail!B2:B3)</f>" in archive.read("xl/worksheets/sheet1.xml")
        assert b"=SUM(A1:A2)" not in archive.read("xl/worksheets/sheet2.xml")


def test_render_workbook_keeps_formula_like_text_in_shared_strings(tmp_path: Path) -> None:
    ir = build_workbook_ir(request())
    output = tmp_path / "formula-like-text.xlsx"

    render_workbook(ir, output)

    with ZipFile(output) as archive:
        shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
        assert "=SUM(A1:A2)" in shared_strings
