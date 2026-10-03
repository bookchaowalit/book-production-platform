"""Reopen and formula-cache QA for XLSX artifacts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from book_production_platform.spreadsheet_ir import WorkbookIR

from .formulas import evaluate_workbook

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _sheet_parts(archive: ZipFile) -> dict[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {element.attrib["Id"]: element.attrib["Target"] for element in relationships}
    parts: dict[str, str] = {}
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        target = rel_targets.get(sheet.attrib[f"{{{NS['rel']}}}id"])
        if target is not None:
            parts[sheet.attrib["name"]] = "xl/" + target.lstrip("/")
    return parts


def qa_workbook(ir: WorkbookIR, output_path: str | Path) -> tuple[str, ...]:
    """Verify the XLSX container, sheets, formulas and cached values."""

    path = Path(output_path)
    issues: list[str] = []
    if not path.exists():
        return ("xlsx_missing",)
    expected = evaluate_workbook(ir)
    try:
        with ZipFile(path) as archive:
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(set(archive.namelist())):
                return ("xlsx_structure_missing",)
            parts = _sheet_parts(archive)
            if not {sheet.name for sheet in ir.sheets}.issubset(parts):
                issues.append("sheet_missing")
            formula_count = 0
            sheets_by_name = {sheet.name: sheet for sheet in ir.sheets}
            for sheet_name, part in parts.items():
                if part not in archive.namelist():
                    issues.append("worksheet_missing")
                    continue
                root = ElementTree.fromstring(archive.read(part))
                sheet_ir = sheets_by_name.get(sheet_name)
                if sheet_ir is not None:
                    validations = sorted(node.attrib.get("sqref", "") for node in root.findall("main:dataValidations/main:dataValidation", NS))
                    if validations != sorted(item.range for item in sheet_ir.data_validations):
                        issues.append(f"data_validation_mismatch:{sheet_name}")
                    rules = sorted(node.attrib.get("sqref", "") for node in root.findall("main:conditionalFormatting", NS))
                    if rules != sorted(item.range for item in sheet_ir.conditional_formats):
                        issues.append(f"conditional_format_mismatch:{sheet_name}")
                for cell in root.findall("main:sheetData/main:row/main:c", NS):
                    formula_node = cell.find("main:f", NS)
                    if formula_node is None:
                        continue
                    formula_count += 1
                    address = f"{sheet_name}!{cell.attrib['r']}"
                    expected_formula = next((item.expression[1:] for item in ir.formulas if item.target == address), None)
                    if expected_formula != (formula_node.text or ""):
                        issues.append(f"formula_mismatch:{address}")
                    cached = cell.find("main:v", NS)
                    if cached is None or address not in expected.values:
                        issues.append(f"formula_cache_missing:{address}")
                    else:
                        wanted = expected.values[address]
                        text = cached.text or ""
                        if isinstance(wanted, bool):
                            if text.upper() not in ({"1", "TRUE"} if wanted else {"0", "FALSE"}):
                                issues.append(f"formula_value_mismatch:{address}")
                        elif isinstance(wanted, str):
                            if text != wanted:
                                issues.append(f"formula_value_mismatch:{address}")
                        else:
                            try:
                                if wanted is None or Decimal(str(wanted)) != Decimal(text or "0"):
                                    issues.append(f"formula_value_mismatch:{address}")
                            except (InvalidOperation, ValueError):
                                issues.append(f"formula_cache_invalid:{address}")
            if formula_count != len(ir.formulas):
                issues.append("formula_count_mismatch")
    except (BadZipFile, OSError, ElementTree.ParseError, KeyError):
        issues.append("xlsx_unreadable")
    return tuple(dict.fromkeys(issues))


__all__ = ["qa_workbook"]
