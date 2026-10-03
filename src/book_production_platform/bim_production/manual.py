"""Manual-reproduction guides for BIM: redo the takeoff (and cost) by hand.

The takeoff guide mirrors the worker and ``reports.py`` exactly: select each
requested IFC class (``by_type`` semantics, so subtypes such as
``IfcWallStandardCase`` are included), read only occurrence-level
``IfcElementQuantity`` sets (type-level quantities are ignored, duplicated
quantity names are ambiguous and never summed), convert to SI, then group by
the exact key ``ifc_class + qto_name + quantity_name + measure_kind + unit +
method`` and sum. Every row and every aggregate the engine published is copied
into the guide, so a person can check each value. UI labels follow Bonsai 0.8
on Blender 4.2 LTS and LibreOffice Calc 24.x; they are not runtime-verified in
this repository's CI.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from book_production_platform.bim_ir import BimTakeoffIR
from book_production_platform.bim_production.reports import CSV_COLUMNS
from book_production_platform.production_trace import ManualGuide

MAX_GUIDE_ROWS = 400
_SI_HINT = {
    "m": "ถ้าโปรแกรมแสดงเป็น mm ให้ × 0.001",
    "m2": "ถ้าโปรแกรมแสดงเป็น mm² ให้ × 0.000001",
    "m3": "ถ้าโปรแกรมแสดงเป็น mm³ ให้ × 0.000000001",
    "count": "จำนวน (ไม่มีหน่วย)",
}


def _key_text(item: Mapping[str, Any]) -> str:
    method = item.get("method") or "(ว่าง)"
    return f"{item['ifc_class']} / {item['qto_name']} / {item['quantity_name']} [{item['unit']}, method={method}]"


def build_takeoff_guide(
    ir: BimTakeoffIR,
    report: Mapping[str, Any] | None = None,
) -> ManualGuide:
    """Translate one takeoff request (and its published report, if any) into manual steps."""

    rows: Sequence[Mapping[str, Any]] = list(report.get("rows", [])) if report else []
    aggregates: Sequence[Mapping[str, Any]] = list(report.get("aggregates", [])) if report else []
    gaps: Sequence[Mapping[str, Any]] = list(report.get("coverage_gaps", [])) if report else []
    classes = ", ".join(ir.element_classes)
    guide = ManualGuide(
        program="Bonsai (BlenderBIM) + LibreOffice Calc",
        program_version="Bonsai 0.8 / Blender 4.2 LTS / Calc 24.x",
        alternatives=[
            "FreeCAD 1.0 BIM workbench",
            "IfcOpenShell 0.8 (Python)",
            "Solibri Office (Information Takeoff)",
            "Autodesk Revit (Schedules)",
        ],
        title=f"Quantity takeoff `{ir.takeoff_id}` ({classes})",
        summary=(
            "เปิดไฟล์ IFC ต้นฉบับ อ่านปริมาณจาก Qto (IfcElementQuantity) ของแต่ละชิ้นงานตาม IFC class ที่ขอ "
            "แปลงเป็นหน่วย SI แล้วรวมยอดตามคีย์เดียวกับเครื่องมือ จะได้ตารางเดียวกับ `takeoff.csv` / `takeoff.json`"
        ),
        estimated_minutes=round(10 + 0.5 * len(rows), 1),
        setup=[
            "ติดตั้ง Blender 4.2 LTS ▸ `Edit ▸ Preferences ▸ Get Extensions` ▸ ค้นหา `Bonsai` ▸ `Install`",
            "ตรวจว่าไฟล์ IFC เป็นไฟล์เดียวกับที่เครื่องมือใช้: `sha256sum <file>.ifc` (Windows: `certutil -hashfile <file>.ifc SHA256`) "
            + (f"ต้องได้ `{report['source_sha256']}`" if report else "แล้วเทียบกับ `inputs` ใน trace.json"),
            "เปิด LibreOffice Calc ไฟล์ใหม่ไว้สำหรับกรอกตาราง (คอลัมน์: " + ", ".join(CSV_COLUMNS) + ")",
        ],
    )

    if report:
        guide.layer(
            "IFC model", str(report.get("ifc_schema", "")),
            content=f"{report.get('total_instances')} instances, เลือก {report.get('selected_elements')} ชิ้น",
            position="source.ifc",
        )
    for aggregate in aggregates:
        count = sum(
            1 for row in rows
            if all(row.get(field) == aggregate.get(field) for field in ("ifc_class", "qto_name", "quantity_name", "measure_kind", "unit", "method"))
        )
        guide.layer(
            f"{aggregate['ifc_class']} · {aggregate['quantity_name']}", str(aggregate["measure_kind"]),
            content=f"ผลรวม {aggregate['value']} {aggregate['unit']} จาก {count} แถว",
            position=f"{aggregate['qto_name']} (method={aggregate.get('method') or '—'})",
        )

    s = "เปิดโมเดล"
    guide.step("เปิดไฟล์ IFC", tool="File ▸ Open IFC Project…", target="Blender + Bonsai", value="<file>.ifc", section=s)
    if report:
        guide.step(
            "ตรวจ schema ของไฟล์", tool="Properties ▸ Scene ▸ Project Overview ▸ Project Info",
            target="IFC Schema", value=str(report.get("ifc_schema", "")), section=s,
        )

    per_class = Counter(str(row["ifc_class"]) for row in rows)
    elements = {
        name: len({str(row["global_id"]) for row in rows if row["ifc_class"] == name}) for name in per_class
    }
    s = "เลือกชิ้นงานตาม IFC class"
    for class_name in ir.element_classes:
        guide.step(
            f"ค้นหาและเลือกทุกชิ้นของ {class_name} (รวม subtype เช่น {class_name}StandardCase ถ้ามี)",
            tool="Properties ▸ Scene ▸ Project Overview ▸ Search ▸ IFC Class", target="Search", value=class_name,
            note="ชื่อแผงอ้างอิง Bonsai 0.8 — ถ้าต่างไป ใช้ IfcOpenShell selector `" + class_name + "` หรือ `Select ▸ Select All by Type` ใน Outliner",
            section=s,
        )
    for occurrence_class, count in sorted(elements.items()):
        guide.step(
            f"ตรวจจำนวนชิ้นที่มีปริมาณของ {occurrence_class}", target="Outliner (ชิ้นที่ถูกเลือก)", value=f"{count} ชิ้น", section=s,
        )

    s = "อ่านปริมาณ (Qto) ทีละชิ้น"
    guide.step(
        "คลิกชิ้นงาน แล้วเปิดแผงชุดปริมาณของ occurrence", tool="Properties ▸ Object ▸ Quantity Sets",
        target="Qto ที่ผูกกับชิ้นนี้โดยตรง (ไม่ใช่ของ Type)",
        note="อ่านเฉพาะ IfcElementQuantity ที่ผูกกับชิ้นงานโดยตรง; ถ้าชื่อปริมาณซ้ำใน Qto เดียวกัน ให้ถือว่า ambiguous และไม่นำมารวม",
        section=s,
    )
    for row in rows[:MAX_GUIDE_ROWS]:
        guide.step(
            f"กรอกแถว: {row['global_id']} · {row['qto_name']} · {row['quantity_name']}",
            target=f"{row['ifc_class']} ({row['measure_kind']}, method={row.get('method') or '—'})",
            value=f"{row['value']} {row['unit']}",
            note=_SI_HINT.get(str(row["unit"]), ""),
            section=s,
        )
    if len(rows) > MAX_GUIDE_ROWS:
        guide.step(
            f"แถวที่เหลือ {len(rows) - MAX_GUIDE_ROWS} แถว: คัดลอกจาก `takeoff.csv` ของเครื่องมือ (คู่มือแสดงแค่ {MAX_GUIDE_ROWS} แถวแรก)",
            section=s,
        )

    s = "รวมยอดตามคีย์ (Pivot Table)"
    guide.step(
        "เลือกตารางทั้งหมดรวมหัวคอลัมน์", target="Calc", value=f"A1:H{len(rows) + 1}", section=s,
    )
    guide.step("สร้าง Pivot Table", tool="Insert ▸ Pivot Table… ▸ Current selection ▸ OK", target="Calc", section=s)
    guide.step(
        "ลากฟิลด์ลง Row Fields ตามลำดับ", target="Pivot Table Layout ▸ Row Fields",
        value="ifc_class, qto_name, quantity_name, measure_kind, unit, method", section=s,
    )
    guide.step("ลาก value ลง Data Fields แล้วตั้งเป็น Sum", target="Pivot Table Layout ▸ Data Fields", value="Sum - value", section=s)
    for aggregate in aggregates:
        guide.step(
            "ตรวจยอดรวม (คัดลอกค่าจากเครื่องมือ ห้ามปัดเศษ)", target=_key_text(aggregate),
            value=f"{aggregate['value']} {aggregate['unit']}", section=s,
        )

    s = "บันทึกตาราง"
    guide.step(
        "บันทึกแถวรายชิ้นเป็น CSV", tool="File ▸ Save As… ▸ File type = Text CSV (.csv)",
        value="takeoff.csv (Character set = Unicode (UTF-8), Field delimiter = `,`, String delimiter = `\"`)",
        note="เทียบค่าไม่ใช่ byte: เครื่องมือใช้บรรทัด CRLF และไม่ใส่เครื่องหมายคำพูดถ้าไม่จำเป็น",
        section=s,
    )

    guide.checks = [
        f"เลือก IFC class ครบตามคำขอ: {classes}",
    ]
    if report:
        guide.checks += [
            f"สถานะผลรวม = `{report.get('status')}` (complete = ไม่มีช่องว่าง, partial = มีบางชิ้นที่อ่านไม่ได้)",
            f"จำนวนแถวรายชิ้น = {len(rows)} และจำนวนยอดรวม = {len(aggregates)}",
            f"schema `{report.get('ifc_schema')}`, instances {report.get('total_instances')}, ชิ้นที่เลือก {report.get('selected_elements')}",
        ]
        guide.checks += [f"{_key_text(item)} = {item['value']}" for item in aggregates]
        if gaps:
            reasons = Counter(str(gap["reason"]) for gap in gaps)
            guide.checks.append(
                "ช่องว่างที่ต้องตามข้อมูลเพิ่ม (ห้ามใส่ 0 แทน): "
                + ", ".join(f"{reason} × {count}" for reason, count in sorted(reasons.items()))
            )
    else:
        guide.checks.append("งานนี้ไม่มี report ที่เผยแพร่ (needs_input หรือ rejected) — ไม่มียอดให้เทียบ ตรวจ Qto ในโมเดลก่อน")
    guide.glossary = {
        "Qto": "Quantity set (IfcElementQuantity) เช่น Qto_WallBaseQuantities",
        "FreeCAD 1.0 BIM": "File ▸ Open (IFC, Native IFC) ▸ เลือกชิ้น ▸ Property view ▸ IFC quantities; รวมยอดด้วย BIM ▸ Manage ▸ IFC Quantities manager",
        "IfcOpenShell": "`ifcopenshell.util.element.get_psets(element, qtos_only=True)` ต่อชิ้น จาก `model.by_type(class)` — ตรงกับที่เครื่องมือทำ",
        "Revit": "View ▸ Schedules ▸ Schedule/Quantities ▸ เลือก Category ▸ Fields ▸ Sorting/Grouping + Calculate totals",
        "Solibri": "Information Takeoff ▸ New Takeoff ▸ Component = class ▸ Columns = Qto quantities ▸ Group by",
    }
    return guide


def build_cost_guide(estimate: Mapping[str, Any]) -> ManualGuide:
    """Spreadsheet steps that reproduce ``cost-estimate.csv`` from the takeoff aggregates."""

    lines: Sequence[Mapping[str, Any]] = list(estimate.get("lines", []))
    currency = str(estimate.get("currency", ""))
    guide = ManualGuide(
        program="LibreOffice Calc",
        program_version="24.x",
        alternatives=["Microsoft Excel 365", "Google Sheets"],
        title=f"Cost estimate `{estimate.get('takeoff_id')}` ({currency})",
        summary=(
            "นำยอดรวมจาก takeoff คูณกับอัตราราคาต่อหน่วยในตารางราคา (จับคู่ด้วยคีย์เดียวกันแบบตรงตัว) "
            "ปัดเงินแต่ละบรรทัดเป็นทศนิยม 2 ตำแหน่ง (ROUND_HALF_UP) แล้วรวม"
        ),
        estimated_minutes=round(5 + 0.5 * len(lines), 1),
        setup=[
            "เปิด `takeoff.json` (ส่วน aggregates) และตารางราคา `bim-cost-rates.v1`",
            "สร้างชีต Calc ใหม่ คอลัมน์: code, ifc_class, qto_name, quantity_name, unit, method, quantity, rate, amount",
        ],
    )
    for line in lines:
        guide.layer(str(line.get("code") or "—"), str(line["ifc_class"]), content=f"{line['quantity']} {line['unit']} × {line['rate']}", position=f"{line['amount']} {currency}")
    s = "กรอกบรรทัดราคา"
    for index, line in enumerate(lines, start=2):
        guide.step(
            f"กรอกบรรทัด {line.get('code') or line['quantity_name']}", target=f"แถว {index}",
            value=f"quantity = {line['quantity']} {line['unit']}, rate = {line['rate']}", section=s,
        )
        guide.step(
            "ใส่สูตรจำนวนเงิน", target=f"I{index}", value=f"=ROUND(G{index}*H{index};2)",
            note=f"ต้องได้ {line['amount']}", section=s,
        )
    s = "รวมยอด"
    if lines:
        guide.step("รวมจำนวนเงินทั้งหมด", target=f"I{len(lines) + 2}", value=f"=SUM(I2:I{len(lines) + 1})", note=f"ต้องได้ {estimate.get('total')}", section=s)
    guide.step(
        "บันทึกเป็น CSV", tool="File ▸ Save As… ▸ Text CSV (.csv)", value="cost-estimate.csv (UTF-8, `,`)", section=s,
    )
    guide.checks = [f"ยอดรวม = {estimate.get('total')} {currency}"]
    guide.checks += [f"{item['ifc_class']} รวม {item['amount']} {currency}" for item in estimate.get("subtotals", [])]
    unpriced = estimate.get("unpriced", [])
    if unpriced:
        guide.checks.append(f"ยังไม่มีราคา {len(unpriced)} รายการ (ห้ามถือว่าเป็น 0): " + "; ".join(_key_text(item) for item in unpriced))
    return guide


__all__ = ["MAX_GUIDE_ROWS", "build_cost_guide", "build_takeoff_guide"]
