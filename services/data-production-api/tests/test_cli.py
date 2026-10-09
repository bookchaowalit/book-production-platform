from __future__ import annotations

import json
from pathlib import Path

from data_production.cli import main


def test_cli_build_writes_manifest(tmp_path: Path, capsys) -> None:
    source = tmp_path / "request.json"
    source.write_text(json.dumps({"dataset_id": "cli", "csv_text": "kind\nA\n"}), encoding="utf-8")
    assert main(["build", "--input", str(source), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-1"]) == 0
    assert "accepted" in capsys.readouterr().out


def _workbook(path: Path) -> Path:
    import pytest

    xlsxwriter = pytest.importorskip("xlsxwriter")
    workbook = xlsxwriter.Workbook(str(path))
    sheet = workbook.add_worksheet("ยอดขาย")
    sheet.write_row(0, 0, ["ภูมิภาค", "ยอดขาย"])
    for index, (region, amount) in enumerate([("เหนือ", 120.5), ("ใต้", 80), ("เหนือ", 30)], start=1):
        sheet.write_row(index, 0, [region, amount])
    workbook.add_worksheet("อื่น ๆ").write(0, 0, "x")
    workbook.close()
    return path


def test_cli_build_reads_an_xlsx_file_directly(tmp_path: Path, capsys) -> None:
    source = _workbook(tmp_path / "sales.xlsx")
    assert main(["build", "--input", str(source), "--output", str(tmp_path / "out"), "--idempotency-key", "xlsx-1"]) == 0
    assert "accepted" in capsys.readouterr().out
    cleaned = next((tmp_path / "out").glob("job-*/cleaned.csv")).read_text(encoding="utf-8")
    assert "เหนือ" in cleaned and "120.5" in cleaned


def test_cli_build_reads_a_thai_windows_csv(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sales.csv"
    source.write_bytes("ภูมิภาค,ยอดขาย\nเหนือ,10\nใต้,5\n".encode("cp874"))
    assert main(["build", "--input", str(source), "--output", str(tmp_path / "out"), "--idempotency-key", "csv-1"]) == 0
    assert "accepted" in capsys.readouterr().out


def test_cli_build_reports_unreadable_table_files(tmp_path: Path, capsys) -> None:
    source = _workbook(tmp_path / "sales.xlsx")
    assert main(["build", "--input", str(source), "--sheet", "ไม่มี", "--output", str(tmp_path / "out"), "--idempotency-key", "x"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "sheet_not_found"
    fake = tmp_path / "fake.xlsx"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    assert main(["build", "--input", str(fake), "--output", str(tmp_path / "out2"), "--idempotency-key", "y"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "encrypted_or_legacy_office"


def test_table_envelope_for_propose(tmp_path: Path) -> None:
    from data_production.file_input import table_envelope

    envelope = table_envelope(_workbook(tmp_path / "Q1 sales.xlsx"), brief="กราฟแท่งยอดขายตามภูมิภาค", sheet="ยอดขาย", locale="th-TH")
    assert envelope["dataset_id"] == "Q1-sales"
    assert envelope["csv_text"].splitlines()[0] == "ภูมิภาค,ยอดขาย"
    assert envelope["brief"] == "กราฟแท่งยอดขายตามภูมิภาค" and envelope["locale"] == "th-TH"
