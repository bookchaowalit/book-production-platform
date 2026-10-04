from __future__ import annotations

import json
from pathlib import Path

import xlsxwriter  # type: ignore[import-untyped]

from book_production_platform.spreadsheet_production.cli import main
from book_production_platform.spreadsheet_production.file_input import table_envelope

PROPOSAL = {
    "template_id": "sales-summary",
    "mapping": {"วันที่": "date", "ภูมิภาค": "category", "ยอดขาย": "amount", "จำนวน": "quantity"},
    "intents": [{"intent": "sum", "column": "amount"}, {"intent": "count", "column": "quantity"}],
}


def workbook(path: Path) -> Path:
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("ยอดขาย")
    sheet.write_row(0, 0, ["วันที่", "ภูมิภาค", "ยอดขาย", "จำนวน", "หมายเหตุ"])
    rows = [("2026-01-05", "เหนือ", 120.5, 2, "เว็บ"), ("2026-01-06", "ใต้", 80.25, 1, None), ("2026-01-07", "เหนือ", 99.99, 3, "หน้าร้าน")]
    for index, row in enumerate(rows, start=1):
        for column, value in enumerate(row):
            if value is not None:
                sheet.write(index, column, value)
    book.close()
    return path


def test_from_file_builds_a_workbook_from_xlsx_without_any_model_call(tmp_path: Path, capsys) -> None:
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps(PROPOSAL, ensure_ascii=False), encoding="utf-8")
    code = main(["from-file", "--input", str(workbook(tmp_path / "sales.xlsx")), "--proposal", str(proposal),
                 "--output", str(tmp_path / "out"), "--idempotency-key", "file-1"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0, summary
    assert summary["proposal"]["model_calls"] == 0
    assert summary["job"]["status"] == "accepted"


def test_from_file_reads_csv_and_reports_bad_files(tmp_path: Path, capsys) -> None:
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps(PROPOSAL, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "sales.csv"
    source.write_bytes("วันที่,ภูมิภาค,ยอดขาย,จำนวน\n2026-01-05,เหนือ,10,1\n".encode("cp874"))
    assert main(["from-file", "--input", str(source), "--proposal", str(proposal), "--output", str(tmp_path / "out"),
                 "--idempotency-key", "file-2"]) == 0
    capsys.readouterr()
    duplicate = tmp_path / "dup.csv"
    duplicate.write_text("a,a\n1,2\n", encoding="utf-8")
    assert main(["from-file", "--input", str(duplicate), "--proposal", str(proposal), "--output", str(tmp_path / "out"),
                 "--idempotency-key", "file-3"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "dataset_header_invalid"


def test_brief_envelope_from_xlsx_keeps_blank_cells_out(tmp_path: Path) -> None:
    envelope = table_envelope(workbook(tmp_path / "Q1 sales.xlsx"), brief="สรุปยอดขายตามภูมิภาค")
    assert envelope["workbook_id"] == "Q1-sales" and envelope["locale"] == "th-TH"
    dataset = envelope["dataset"]
    assert dataset["name"] == "ยอดขาย" and dataset["header"][0] == "วันที่"
    assert dataset["rows"][1] == {"วันที่": "2026-01-06", "ภูมิภาค": "ใต้", "ยอดขาย": "80.25", "จำนวน": "1"}
