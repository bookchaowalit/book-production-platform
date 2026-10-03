"""Manual-reproduction guide: build the same workbook by hand in Microsoft Excel.

The steps mirror :func:`.render.render_workbook` exactly — sheet order and
names, header row, title style, typed cells, number formats, column widths,
frozen top row, dropdown validations, conditional formats, named ranges, and
every formula cell with the exact text the engine wrote. Expected results are
the engine's cached values (from :func:`.formulas.evaluate_workbook`), so a
person can check their file against ``workbook.xlsx`` cell by cell.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from book_production_platform.production_trace import ManualGuide
from book_production_platform.spreadsheet_ir import CellIR, SheetIR, WorkbookIR

from .formulas import Scalar, evaluate_workbook
from .render import (
    DATA_COLUMN_WIDTH,
    FORMULA_FILL,
    FREEZE_ROWS,
    HEADER_STYLE,
    SUMMARY_COLUMN_WIDTHS,
    SUMMARY_SHEET,
    TITLE_STYLE,
    absolute_reference,
    column_number,
    formula_number_format,
    header_row,
    number_text,
    split_cell,
)

PROGRAM = "Microsoft Excel"
WORKBOOK_FILENAME = "workbook.xlsx"
MAX_TYPED_ROWS = 200  # beyond this a sheet's data rows are copied from the engine's file
_RISKY_TEXT = re.compile(r"^\s*[=+\-@]|^\s*[\d\s.,:/%$฿€£¥()-]+\s*$|^\s*(true|false)\s*$", re.IGNORECASE)
_OPERATOR_LABELS = {
    "greater_than": "greater than",
    "greater_than_or_equal": "greater than or equal to",
    "less_than": "less than",
    "less_than_or_equal": "less than or equal to",
    "equal_to": "equal to",
    "not_equal_to": "not equal to",
    "between": "between",
}


def _column_name(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


def typed_text(cell: CellIR) -> str:
    """What a person types into the cell so Excel stores the same typed value."""

    value = cell.value
    if cell.data_type == "null" or value is None:
        return ""
    if cell.data_type == "boolean":
        return "TRUE" if value else "FALSE"
    if cell.data_type == "date" and isinstance(value, date):
        return value.isoformat()
    if cell.data_type in {"integer", "decimal"} and isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return number_text(Decimal(value)) if isinstance(value, Decimal) else str(value)
    text = str(value)
    # A leading apostrophe makes Excel keep the text literally (it is not shown in the cell).
    return "'" + text if _RISKY_TEXT.search(text) else text


def display_value(value: Scalar) -> str:
    if value is None:
        return "(ว่าง)"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return number_text(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _used_range(sheet: SheetIR, formula_targets: list[str]) -> str:
    cells = [cell.address.split("!", 1)[1] for cell in sheet.cells] + [target.split("!", 1)[1] for target in formula_targets]
    if not cells:
        return "A1"
    parsed = [split_cell(cell) for cell in cells]
    max_col = max(column_number(letters) for letters, _ in parsed)
    max_row = max(row for _, row in parsed)
    return f"A1:{_column_name(max_col)}{max_row}"


def _row_of(address: str) -> int:
    return split_cell(address.split("!", 1)[1])[1]


def _sheet_steps(guide: ManualGuide, sheet: SheetIR, order: int, formula_targets: set[str]) -> None:
    section = f"ชีต {order}: {sheet.name}"
    if order == 1:
        guide.step("ดับเบิลคลิกแท็บชีตแรก (Sheet1) ที่มุมล่างซ้าย แล้วพิมพ์ชื่อใหม่ กด Enter", tool="Sheet tab ▸ Rename", target="แท็บที่ 1", value=sheet.name, section=section)
    else:
        guide.step("คลิกปุ่ม ⊕ (New sheet) ทางขวาของแท็บสุดท้าย แล้วดับเบิลคลิกแท็บใหม่เพื่อเปลี่ยนชื่อ", tool="Sheet tab ▸ New sheet (+) ▸ Rename", target=f"แท็บที่ {order}", value=sheet.name, section=section)

    by_row: dict[int, list[CellIR]] = {}
    for cell in sheet.cells:
        if cell.address in formula_targets:
            continue
        by_row.setdefault(_row_of(cell.address), []).append(cell)
    headers_at = header_row(sheet.name)
    label_count = len(sheet.column_labels)

    if sheet.name == SUMMARY_SHEET:
        title = next((cell for cell in sheet.cells if cell.address == f"{SUMMARY_SHEET}!A1"), None)
        if title is not None:
            guide.step("คลิกเซลล์ A1 แล้วพิมพ์ชื่อสมุดงาน กด Enter", target=f"{SUMMARY_SHEET}!A1", value=typed_text(title), section=section)
            guide.step("จัดรูปแบบชื่อ: ตัวหนาและขนาดตัวอักษร", tool="Home ▸ Font ▸ Bold (Ctrl+B) และ Home ▸ Font ▸ Font Size", target="A1",
                       value=f"Bold, {TITLE_STYLE['font_size']} pt", section=section)
            by_row.pop(1, None)

    if label_count:
        header_range = f"A{headers_at}:{_column_name(label_count)}{headers_at}"
        labels = "; ".join(f"{_column_name(index)}{headers_at}={label}" for index, label in enumerate(sheet.column_labels, start=1))
        guide.step("พิมพ์หัวคอลัมน์ (กด Tab เพื่อไปเซลล์ถัดไป)", target=header_range, value=labels, section=section)
        guide.step("เลือกแถวหัวคอลัมน์แล้วทำตัวหนา", tool="Home ▸ Font ▸ Bold (Ctrl+B)", target=header_range, section=section)
        guide.step("ใส่สีพื้นหัวคอลัมน์", tool="Home ▸ Font ▸ Fill Color ▸ More Colors… ▸ Custom ▸ Hex", target=header_range,
                   value=str(HEADER_STYLE["bg_color"]), section=section)
        guide.step("ใส่เส้นขอบบางรอบทุกเซลล์หัวคอลัมน์", tool="Home ▸ Font ▸ Borders ▸ All Borders", target=header_range, section=section)
        by_row.pop(headers_at, None)

    data_rows = sorted(by_row)
    if len(data_rows) > MAX_TYPED_ROWS:
        last_col = max(column_number(split_cell(cell.address.split("!", 1)[1])[0]) for row in data_rows for cell in by_row[row])
        block = f"A{data_rows[0]}:{_column_name(last_col)}{data_rows[-1]}"
        guide.step(
            f"ข้อมูลมี {len(data_rows)} แถว (เกิน {MAX_TYPED_ROWS}) — คัดลอกช่วงนี้จาก {WORKBOOK_FILENAME} ของเครื่องมือ แล้ววางเป็นค่า",
            tool="Ctrl+C ▸ Home ▸ Paste ▸ Paste Special… ▸ Values", target=f"{sheet.name}!{block}", value=block, section=section,
        )
    else:
        for row in data_rows:
            cells = sorted(by_row[row], key=lambda item: column_number(split_cell(item.address.split("!", 1)[1])[0]))
            typed = [f"{cell.address.split('!', 1)[1]}={typed_text(cell)}" for cell in cells if typed_text(cell) != ""]
            if typed:
                guide.step(f"พิมพ์ข้อมูลแถว {row} (เซลล์ที่ไม่ได้ระบุให้เว้นว่าง)", target=f"แถว {row}", value="; ".join(typed), section=section)

    text_columns = sorted({
        split_cell(cell.address.split("!", 1)[1])[0]
        for cell in sheet.cells
        if cell.data_type in {"text", "boolean"} and _row_of(cell.address) not in {1, headers_at} and cell.address not in formula_targets
    }, key=column_number)
    for letters in text_columns:
        rows = [
            _row_of(cell.address) for cell in sheet.cells
            if cell.data_type in {"text", "boolean"} and cell.address.split("!", 1)[1].rstrip("0123456789") == letters
            and _row_of(cell.address) not in {1, headers_at} and cell.address not in formula_targets
        ]
        if rows:
            guide.step("ตั้งให้ข้อความตัดบรรทัดในเซลล์", tool="Home ▸ Alignment ▸ Wrap Text", target=f"{letters}{min(rows)}:{letters}{max(rows)}", section=section)

    for item in sheet.column_formats:
        guide.step(
            "เลือกช่วงข้อมูลของคอลัมน์ แล้วตั้งรูปแบบตัวเลขแบบกำหนดเอง (พิมพ์ตรงตามนี้ทุกตัวอักษร)",
            tool="Home ▸ Number ▸ Number Format ▸ More Number Formats… ▸ Custom ▸ Type",
            target=f"{sheet.name}!{item.range}", value=item.number_format, section=section,
        )

    if sheet.name == SUMMARY_SHEET:
        guide.step("ตั้งความกว้างคอลัมน์ A", tool="Home ▸ Cells ▸ Format ▸ Column Width…", target="คอลัมน์ A", value=str(SUMMARY_COLUMN_WIDTHS[0]), section=section)
        guide.step("ตั้งความกว้างคอลัมน์ B", tool="Home ▸ Cells ▸ Format ▸ Column Width…", target="คอลัมน์ B", value=str(SUMMARY_COLUMN_WIDTHS[1]), section=section)
    else:
        last = _column_name(max(1, label_count))
        guide.step("เลือกคอลัมน์ทั้งหมดที่ใช้ แล้วตั้งความกว้าง", tool="Home ▸ Cells ▸ Format ▸ Column Width…", target=f"คอลัมน์ A:{last}",
                   value=str(DATA_COLUMN_WIDTH), section=section)
    guide.step(f"ตรึงแถวบนสุด {FREEZE_ROWS} แถว", tool="View ▸ Window ▸ Freeze Panes ▸ Freeze Top Row", target=sheet.name, section=section)

    for validation in sheet.data_validations:
        guide.step(
            "เลือกช่วงแล้วสร้างรายการดรอปดาวน์ (ติ๊ก Ignore blank และ In-cell dropdown)",
            tool="Data ▸ Data Tools ▸ Data Validation… ▸ Settings ▸ Allow: List ▸ Source",
            target=f"{sheet.name}!{validation.range}", value=",".join(validation.allowed_values), section=section,
        )
    for rule in sheet.conditional_formats:
        value = number_text(rule.value) if rule.value2 is None else f"{number_text(rule.value)} and {number_text(rule.value2)}"
        guide.step(
            "เลือกช่วงแล้วสร้างกฎไฮไลต์: Format only cells that contain ▸ Cell Value ▸ เงื่อนไข ▸ ค่า",
            tool="Home ▸ Styles ▸ Conditional Formatting ▸ New Rule…",
            target=f"{sheet.name}!{rule.range}", value=f"Cell Value {_OPERATOR_LABELS[rule.operator]} {value}", section=section,
        )
        guide.step("กำหนดสีพื้นของกฎนี้ แล้วกด OK สองครั้ง", tool="Format… ▸ Fill ▸ More Colors… ▸ Custom ▸ Hex", target=f"{sheet.name}!{rule.range}",
                   value=rule.fill_color, section=section)


def build_excel_guide(ir: WorkbookIR, *, filename: str = WORKBOOK_FILENAME) -> ManualGuide:
    """Translate a validated WorkbookIR into numbered Excel steps and a sheet map."""

    values = evaluate_workbook(ir).values
    guide = ManualGuide(
        program=PROGRAM,
        program_version="365",
        alternatives=["Google Sheets", "LibreOffice Calc"],
        title=ir.title,
        summary=(
            f"ทำตามขั้นตอนนี้ใน Excel จะได้สมุดงานเหมือน `{filename}` ที่เครื่องมือสร้าง: ชีต เซลล์ รูปแบบตัวเลข "
            "สูตร (ข้อความสูตรตรงทุกตัวอักษร) ชื่อช่วง ดรอปดาวน์ และกฎไฮไลต์ จากนั้นเทียบค่าที่คำนวณได้กับค่าของเครื่องมือ"
        ),
        setup=[
            "เปิด Microsoft Excel ▸ `File ▸ New ▸ Blank workbook`",
            "ตั้งให้คำนวณอัตโนมัติ: `Formulas ▸ Calculation ▸ Calculation Options ▸ Automatic`",
            "สูตรใช้ชื่อฟังก์ชันภาษาอังกฤษและคั่นอาร์กิวเมนต์ด้วยจุลภาค `,` — ถ้า Excel ตั้งภูมิภาคให้ใช้ `;` ให้แทน `,` ด้วย `;` ตอนพิมพ์สูตร",
            "ข้อความที่ขึ้นต้นด้วย `'` ในคู่มือนี้: พิมพ์ `'` นำหน้าด้วย เพื่อให้ Excel เก็บเป็นข้อความตรงตัว (เครื่องหมาย `'` จะไม่แสดงในเซลล์)",
            "ชีตทั้งหมดต้องสร้างก่อนพิมพ์สูตร เพราะสูตรอ้างอิงข้ามชีต",
        ],
    )
    formulas_by_sheet: dict[str, list[str]] = {}
    for formula in ir.formulas:
        formulas_by_sheet.setdefault(formula.target.split("!", 1)[0], []).append(formula.target)
    for order, sheet in enumerate(ir.sheets, start=1):
        data_rows = len({_row_of(cell.address) for cell in sheet.cells} - {1, header_row(sheet.name)})
        content = f"{len(sheet.column_labels)} คอลัมน์, {data_rows} แถวข้อมูล, {len(formulas_by_sheet.get(sheet.name, []))} สูตร"
        extras = [f"{len(sheet.data_validations)} ดรอปดาวน์" if sheet.data_validations else "", f"{len(sheet.conditional_formats)} กฎไฮไลต์" if sheet.conditional_formats else ""]
        content += "".join(f", {item}" for item in extras if item)
        guide.layer(sheet.name, "Worksheet", content=content, position=f"แท็บที่ {order}; ช่วงใช้งาน {_used_range(sheet, formulas_by_sheet.get(sheet.name, []))}")

    formula_targets = {formula.target for formula in ir.formulas}
    for order, sheet in enumerate(ir.sheets, start=1):
        _sheet_steps(guide, sheet, order, formula_targets)

    for name, reference in ir.named_ranges.items():
        guide.layer(name, "Named range", content=reference, position="Workbook")
        guide.step("สร้างชื่อช่วง (Scope: Workbook)", tool="Formulas ▸ Defined Names ▸ Name Manager ▸ New… ▸ Name / Refers to",
                   target=name, value=absolute_reference(reference), section="ชื่อช่วง (Named ranges)")

    for formula in ir.formulas:
        cell = formula.target
        expected = display_value(values.get(cell))
        guide.step(
            "คลิกเซลล์ พิมพ์สูตรตรงตามนี้ทุกตัวอักษร แล้วกด Enter (พิมพ์ทีละเซลล์ — ห้ามลาก Fill Handle เพราะช่วงอ้างอิงจะเลื่อน)",
            tool="Formula Bar", target=cell, value=formula.expression, note=f"ค่าที่ต้องได้ = {expected}", section="สูตร (Formulas)",
        )
        number_format = formula_number_format(formula)
        style = f"Bold; Fill Color {FORMULA_FILL}" + (f"; Number Format Custom {number_format}" if number_format else "; Number Format General")
        guide.step("จัดรูปแบบเซลล์สูตร", tool="Home ▸ Font ▸ Bold; Home ▸ Font ▸ Fill Color ▸ More Colors… ▸ Hex; Home ▸ Number ▸ More Number Formats… ▸ Custom",
                   target=cell, value=style, section="สูตร (Formulas)")

    guide.step("ใส่ชื่อเรื่องในคุณสมบัติไฟล์", tool="File ▸ Info ▸ Properties ▸ Title", value=ir.title, section="บันทึก")
    guide.step("คลิกแท็บชีตแรกให้เป็นชีตที่เปิดอยู่ แล้วบันทึกเป็น .xlsx", tool="File ▸ Save As ▸ Excel Workbook (*.xlsx)", value=filename, section="บันทึก")

    guide.checks = [
        "แท็บชีตเรียงตามนี้: " + ", ".join(f"{order}. {sheet.name}" for order, sheet in enumerate(ir.sheets, start=1)),
        f"มีสูตรทั้งหมด {len(ir.formulas)} เซลล์; กด `Formulas ▸ Formula Auditing ▸ Show Formulas` (Ctrl+`) แล้วข้อความสูตรตรงกับ `{filename}` ทุกเซลล์",
        *(f"{formula.target} แสดงค่า {display_value(values.get(formula.target))}" for formula in ir.formulas),
        f"`Formulas ▸ Name Manager` มี {len(ir.named_ranges)} ชื่อ: " + (", ".join(ir.named_ranges) or "—"),
        (
            f"ดรอปดาวน์ {sum(len(sheet.data_validations) for sheet in ir.sheets)} ช่วง และกฎไฮไลต์ {sum(len(sheet.conditional_formats) for sheet in ir.sheets)} กฎ "
            "(ตรวจที่ `Home ▸ Conditional Formatting ▸ Manage Rules…` ▸ Show formatting rules for: This Worksheet)"
        ),
        "ทุกชีตตรึงแถวบนสุดไว้ (เลื่อนลงแล้วหัวคอลัมน์ยังอยู่)",
        "ข้อความที่ขึ้นต้นด้วย = + - @ ใน Detail ยังเป็นข้อความ ไม่ถูกคำนวณเป็นสูตร",
    ]
    guide.glossary = {
        "Google Sheets": "Format ▸ Number ▸ Custom number format; Data ▸ Data validation ▸ Dropdown; Format ▸ Conditional formatting; "
        "Data ▸ Named ranges; View ▸ Freeze ▸ 1 row; File ▸ Download ▸ Microsoft Excel (.xlsx)",
        "LibreOffice Calc": "Format ▸ Cells… ▸ Numbers ▸ Format code; Data ▸ Validity… ▸ Allow: List; Format ▸ Conditional ▸ Condition…; "
        "Sheet ▸ Named Ranges and Expressions ▸ Define…; View ▸ Freeze Cells ▸ Freeze First Row; File ▸ Save As ▸ Excel 2007-365 (.xlsx)",
        "Cached value": "ค่าผลลัพธ์ของสูตรที่เครื่องมือคำนวณและเก็บไว้ในไฟล์ — Excel ต้องคำนวณได้ค่าเดียวกัน",
    }
    typed_cells = sum(len(sheet.cells) for sheet in ir.sheets)
    guide.estimated_minutes = round(5 + 0.4 * len(guide.steps) + 0.05 * typed_cells, 1)
    return guide


__all__ = ["MAX_TYPED_ROWS", "PROGRAM", "build_excel_guide", "display_value", "typed_text"]
