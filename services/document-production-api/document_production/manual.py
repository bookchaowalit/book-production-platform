"""Manual-reproduction guide for Documents: how to build the same file in Microsoft Word.

The steps mirror :func:`document_production.renderers._render_docx` exactly
(margins, heading levels, header lines, ``Table Grid`` tables, 6.3-inch images,
Totals block), so a person following them by hand gets the same structure as
``document.docx``; the totals are copied from the engine, never recalculated.
"""

from __future__ import annotations

from document_ir import DocumentIR
from production_trace import ManualGuide

from .renderers import _footer_label, _header_lines


def build_word_guide(ir: DocumentIR) -> ManualGuide:
    """Translate a validated DocumentIR into numbered Word steps and a layer map."""

    guide = ManualGuide(
        program="Microsoft Word",
        program_version="365",
        alternatives=["LibreOffice Writer", "Google Docs"],
        title=ir.title,
        summary="ทำตามขั้นตอนนี้ใน Word จะได้โครงเอกสารเหมือน `document.docx` ที่เครื่องมือสร้าง; จากนั้น Export เป็น PDF",
        setup=[
            "เปิด Microsoft Word ▸ `File ▸ New ▸ Blank document`",
            "ถ้าเอกสารเป็นภาษาไทย ให้ใช้ฟอนต์ที่รองรับไทย เช่น TH Sarabun New หรือ Sarabun",
        ],
    )
    guide.step("ตั้งระยะขอบบน-ล่าง", tool="Layout ▸ Margins ▸ Custom Margins…", target="Top / Bottom", value='0.7"', section="ตั้งค่าหน้า")
    guide.step("ตั้งขนาดกระดาษ", tool="Layout ▸ Size", value="A4", section="ตั้งค่าหน้า")
    footer_label = _footer_label(ir)
    guide.layer("Footer", "Footer (right aligned)", content=f"{footer_label}<PAGE>", position="ท้ายทุกหน้า ชิดขวา")
    guide.step("เปิดส่วนท้ายกระดาษ", tool="Insert ▸ Footer ▸ Edit Footer", section="ตั้งค่าหน้า")
    guide.step("พิมพ์ข้อความนำหน้าเลขหน้า แล้วจัดชิดขวา (Ctrl+R)", tool="Home ▸ Paragraph ▸ Align Right", value=footer_label, section="ตั้งค่าหน้า")
    guide.step("แทรกเลขหน้า ณ ตำแหน่งเคอร์เซอร์", tool="Insert ▸ Page Number ▸ Current Position ▸ Plain Number", section="ตั้งค่าหน้า")
    guide.step("ปิดส่วนท้าย", tool="Header & Footer ▸ Close Header and Footer", section="ตั้งค่าหน้า")

    guide.layer("ชื่อเอกสาร", "Title (Heading 0)", content=ir.title, position="บนสุด")
    guide.step("พิมพ์ชื่อเอกสาร แล้วเลือกสไตล์ Title", tool="Home ▸ Styles ▸ Title", target="บรรทัดแรก", value=ir.title, section="หัวเอกสาร")
    for line in _header_lines(ir):
        guide.layer("บรรทัดหัวเอกสาร", "Normal paragraph", content=line)
        guide.step("กด Enter แล้วพิมพ์บรรทัดหัวเอกสาร (สไตล์ Normal)", tool="Home ▸ Styles ▸ Normal", value=line, section="หัวเอกสาร")

    for index, section in enumerate(ir.sections, start=1):
        label = f"ส่วนที่ {index}: {section.heading}"
        guide.layer(section.heading, "Heading 1", content=f"{len(section.paragraphs)} ย่อหน้า, {len(section.tables)} ตาราง, {len(section.images)} รูป")
        guide.step("พิมพ์หัวข้อส่วน แล้วเลือก Heading 1", tool="Home ▸ Styles ▸ Heading 1", value=section.heading, section=label)
        for paragraph in section.paragraphs:
            guide.step("พิมพ์ย่อหน้า (สไตล์ Normal)", tool="Home ▸ Styles ▸ Normal", value=paragraph, section=label)
        for table in section.tables:
            rows = len(table.rows) + 1
            guide.layer(f"ตาราง {section.heading}", "Table (Table Grid)", content=f"{len(table.headers)} คอลัมน์ × {rows} แถว")
            guide.step(
                "แทรกตาราง",
                tool="Insert ▸ Table ▸ Insert Table…",
                value=f"Number of columns = {len(table.headers)}, Number of rows = {rows}",
                section=label,
            )
            guide.step("เลือกสไตล์ตาราง", tool="Table Design ▸ Table Styles ▸ Table Grid", target="ตารางที่เพิ่งแทรก", section=label)
            guide.step("พิมพ์หัวคอลัมน์ในแถวแรก (กด Tab เพื่อไปช่องถัดไป)", target="แถว 1", value=" | ".join(table.headers), section=label)
            for row_number, row in enumerate(table.rows, start=2):
                guide.step(f"พิมพ์ข้อมูลแถว {row_number}", target=f"แถว {row_number}", value=" | ".join(row.cells), section=label)
        for image in section.images:
            guide.layer("รูปภาพ", "Inline picture", content=image.path.split("/")[-1], position='กว้าง 6.3"')
            guide.step("แทรกรูป", tool="Insert ▸ Pictures ▸ This Device…", value=image.path.split("/")[-1], section=label)
            guide.step("ตั้งความกว้างรูป (ล็อกสัดส่วน)", tool="Picture Format ▸ Size ▸ Width", value='6.3"', section=label)
            if image.caption:
                guide.step("พิมพ์คำบรรยายใต้รูป (สไตล์ Normal)", value=image.caption, section=label)

    if ir.derived_totals:
        guide.layer("Totals", "Heading 1 + paragraphs", content=", ".join(ir.derived_totals))
        guide.step("พิมพ์หัวข้อ Totals แล้วเลือก Heading 1", tool="Home ▸ Styles ▸ Heading 1", value="Totals", section="ยอดรวม")
        for name, value in ir.derived_totals.items():
            guide.step(
                "พิมพ์ยอดตามที่เครื่องมือคำนวณ (อย่าคำนวณใหม่เอง)",
                value=f"{name}: {value} {ir.currency}",
                section="ยอดรวม",
            )

    guide.step("บันทึกเป็น Word", tool="File ▸ Save As", value="document.docx", section="บันทึกและส่งออก")
    guide.step("ส่งออก PDF", tool="File ▸ Export ▸ Create PDF/XPS", value="document.pdf", section="บันทึกและส่งออก")
    guide.checks = [
        f"มี {len(ir.sections)} หัวข้อระดับ Heading 1" + (" + Totals" if ir.derived_totals else ""),
        "ตัวเลขทุกตัวตรงกับ `document.docx` ที่เครื่องมือสร้าง (เปิดคู่กันแล้วเทียบ)",
        "ภาษาไทยไม่มีสี่เหลี่ยม/ตัวอักษรเพี้ยนใน PDF",
    ]
    guide.estimated_minutes = round(5 + 0.5 * len(guide.steps), 1)
    return guide


__all__ = ["build_word_guide"]
