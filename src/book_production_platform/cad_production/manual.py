"""Manual-reproduction guide for CAD: build the same plate by hand in FreeCAD 1.0.

The steps mirror ``freecad_worker.FCMacro`` exactly: one ``Body``; ``PlateSketch``
on the XY plane with a rectangle from the origin (4 coincident + 2 horizontal +
2 vertical + X/Y distance 0 on the corner + width + height = 12 constraints);
``Pad`` of the plate thickness; ``HoleSketch`` at Z = thickness with four
circles (X, Y, and diameter per circle = 12 constraints); ``Pocket`` Through
all. No fillets or chamfers are made by ``mounting-plate.v1``, so none appear
here. Values are copied from PartIR; checks compare against the analytical
invariants and, when available, the engine's measured QA values.
"""

from __future__ import annotations

from book_production_platform.cad_ir import PartIR
from book_production_platform.cad_production.properties import (
    MATERIAL_DENSITIES_G_CM3,
    mm,
    plate_properties,
)
from book_production_platform.cad_production.qa import CadQAResult
from book_production_platform.production_trace import ManualGuide

FREECAD_VERSION = "1.0"


def build_freecad_guide(part: PartIR, qa: CadQAResult | None = None) -> ManualGuide:
    """Translate one validated PartIR into click-level FreeCAD Part Design steps."""

    props = plate_properties(part)
    d = props.dimensions
    w, h, t = mm(d.width_mm), mm(d.height_mm), mm(d.thickness_mm)
    dia, ox, oy = mm(d.hole_diameter_mm), mm(d.hole_offset_x_mm), mm(d.hole_offset_y_mm)

    guide = ManualGuide(
        program="FreeCAD",
        program_version=f"{FREECAD_VERSION} (Part Design workbench)",
        alternatives=["Autodesk Fusion 360", "Onshape", "SolidWorks"],
        title=f"Mounting plate `{part.part_id}` ({part.template}, {part.hole_pattern})",
        summary=(
            f"แผ่นยึดสี่เหลี่ยม {w} × {h} × {t} mm มีรูทะลุ 4 รู Ø{dia} mm ห่างขอบ X {ox} mm / Y {oy} mm "
            "ทำตามขั้นตอนนี้ใน FreeCAD จะได้ feature tree เดียวกับ `part.FCStd` ที่เครื่องมือสร้าง "
            "(Body ▸ PlateSketch ▸ Pad ▸ HoleSketch ▸ Pocket) แล้ว export เป็น STEP/STL"
        ),
        estimated_minutes=12,
        setup=[
            f"เปิด FreeCAD {FREECAD_VERSION} ▸ `File ▸ New` (เอกสารใหม่)",
            (
                "ตั้งหน่วย: `Edit ▸ Preferences… ▸ General ▸ Units` ▸ Unit system = `Standard (mm, kg, s, degree)`, "
                "Number of decimals = `3`"
            ),
            "สลับ workbench: `View ▸ Workbench ▸ Part Design`",
            "ตรวจว่าเปิด `View ▸ Panels ▸ Report view` และ `Python console` ไว้ (ใช้ตรวจค่าในขั้นสุดท้าย)",
        ],
    )

    guide.layer("Body", "PartDesign::Body", content="1 body, Tip = Pocket", position="Model tree ระดับบนสุด")
    guide.layer(
        "PlateSketch", "Sketcher::SketchObject", content="สี่เหลี่ยม 4 เส้น, 12 constraints",
        position="XY_Plane, มุมล่างซ้ายที่ (0, 0)",
    )
    guide.layer("Pad", "PartDesign::Pad", content=f"Length = {t} mm (Dimension)", position="ขึ้นจาก Z = 0 ถึง Z = " + t)
    guide.layer(
        "HoleSketch", "Sketcher::SketchObject", content=f"วงกลม 4 วง Ø{dia}, 12 constraints",
        position=f"XY_Plane + Attachment Offset Z = {t}",
    )
    guide.layer("Pocket", "PartDesign::Pocket", content="Type = Through all", position="ตัดลงทะลุความหนา")

    s = "Body"
    guide.step("สร้าง Body ใหม่", tool="Part Design ▸ Create body", target="Model tree", section=s)
    guide.step("เปลี่ยนชื่อเป็น Body (คลิกขวา ▸ Rename หรือกด F2)", target="Model tree ▸ Body", value="Body", section=s)

    s = "PlateSketch: สเก็ตช์แผ่น"
    guide.step("สร้างสเก็ตช์ใหม่", tool="Part Design ▸ Create sketch", target="หน้าต่าง Select feature ▸ XY_Plane", value="XY_Plane", section=s)
    guide.step("เปลี่ยนชื่อสเก็ตช์", target="Model tree ▸ Sketch", value="PlateSketch", section=s)
    guide.step(
        "วาดสี่เหลี่ยม: คลิกจุดแรกใกล้ origin แล้วคลิกมุมตรงข้ามทางขวาบน",
        tool="Sketch ▸ Sketcher geometries ▸ Create rectangle", target="3D view (มุมล่างซ้ายใกล้จุด origin)",
        note="เครื่องมือ rectangle ใส่ constraint ให้อัตโนมัติ 8 ตัว (Coincident 4, Horizontal 2, Vertical 2)",
        section=s,
    )
    guide.step(
        "เลือกจุดมุมล่างซ้ายของสี่เหลี่ยม แล้วตั้งระยะแนวนอนจาก origin",
        tool="Sketch ▸ Sketcher constraints ▸ Constrain horizontal distance", target="จุดมุมล่างซ้าย",
        value="0.000 mm", section=s,
    )
    guide.step(
        "เลือกจุดเดิม แล้วตั้งระยะแนวตั้งจาก origin",
        tool="Sketch ▸ Sketcher constraints ▸ Constrain vertical distance", target="จุดมุมล่างซ้าย",
        value="0.000 mm", section=s,
    )
    guide.step(
        "เลือกเส้นล่าง แล้วตั้งความยาว (ความกว้างแผ่น)", tool="Sketch ▸ Sketcher constraints ▸ Constrain distance",
        target="เส้นล่าง (แนวแกน X)", value=f"{w} mm", section=s,
    )
    guide.step(
        "เลือกเส้นขวา แล้วตั้งความยาว (ความสูงแผ่น)", tool="Sketch ▸ Sketcher constraints ▸ Constrain distance",
        target="เส้นขวา (แนวแกน Y)", value=f"{h} mm", section=s,
    )
    guide.step(
        "ตรวจว่า Solver messages ขึ้น `Fully constrained` และมี 12 constraints แล้วปิดสเก็ตช์",
        tool="Close (ปุ่มใน Tasks panel)", target="Tasks panel ▸ Solver messages / Constraints", value="12 constraints",
        section=s,
    )

    s = "Pad: ขึ้นรูปความหนา"
    guide.step("เลือก PlateSketch แล้วสร้าง Pad", tool="Part Design ▸ Create an additive feature ▸ Pad", target="Model tree ▸ PlateSketch", section=s)
    guide.step("ตั้ง Type", target="Tasks panel ▸ Pad parameters ▸ Type", value="Dimension", section=s)
    guide.step("ตั้งความหนา", target="Tasks panel ▸ Pad parameters ▸ Length", value=f"{t} mm", section=s)
    guide.step("ไม่ติ๊ก Reversed / Symmetric to plane แล้วกด OK", target="Tasks panel", value="Reversed = off, Symmetric = off", section=s)

    s = "HoleSketch: สเก็ตช์รู"
    guide.step(
        "คลิกพื้นที่ว่างให้ไม่มีอะไรถูกเลือก แล้วสร้างสเก็ตช์ใหม่บน XY_Plane",
        tool="Part Design ▸ Create sketch", target="หน้าต่าง Select feature ▸ XY_Plane", value="XY_Plane", section=s,
    )
    guide.step("เปลี่ยนชื่อสเก็ตช์", target="Model tree ▸ Sketch001", value="HoleSketch", section=s)
    guide.step(
        "ยกสเก็ตช์ขึ้นไปที่ผิวบนของแผ่น", tool="Property view ▸ Data ▸ Attachment ▸ Attachment Offset ▸ Position ▸ z",
        target="HoleSketch", value=f"{t} mm",
        note="เครื่องมือวางสเก็ตช์ที่ Z = ความหนา โดยแกน X/Y ตรงกับแกนโลก (ไม่ attach กับ face เพื่อไม่ให้แกนหมุน)",
        section=s,
    )
    guide.step("เปิดแก้สเก็ตช์ (ดับเบิลคลิก HoleSketch)", target="Model tree ▸ HoleSketch", section=s)
    for hole in props.hole_centers:
        x, y = mm(hole.x_mm), mm(hole.y_mm)
        guide.layer(f"รู {hole.label}", "Circle in HoleSketch", content=f"Ø{dia} mm", position=f"X = {x}, Y = {y}")
        guide.step(
            f"วาดวงกลม {hole.label}: คลิกจุดศูนย์กลางใกล้ตำแหน่ง แล้วคลิกรัศมี",
            tool="Sketch ▸ Sketcher geometries ▸ Create circle", target=f"ใกล้ ({x}, {y})", section=s,
        )
        guide.step(
            f"เลือกจุดศูนย์กลาง {hole.label} แล้วตั้งระยะแนวนอนจาก origin",
            tool="Sketch ▸ Sketcher constraints ▸ Constrain horizontal distance", target=f"center {hole.label}",
            value=f"{x} mm", section=s,
        )
        guide.step(
            f"เลือกจุดศูนย์กลาง {hole.label} แล้วตั้งระยะแนวตั้งจาก origin",
            tool="Sketch ▸ Sketcher constraints ▸ Constrain vertical distance", target=f"center {hole.label}",
            value=f"{y} mm", section=s,
        )
        guide.step(
            f"เลือกเส้นรอบวง {hole.label} แล้วตั้งเส้นผ่านศูนย์กลาง",
            tool="Sketch ▸ Sketcher constraints ▸ Constrain diameter", target=f"circle {hole.label}",
            value=f"{dia} mm", section=s,
        )
    guide.step(
        "ตรวจ `Fully constrained` และ 12 constraints (3 ต่อรู × 4 รู) แล้วปิดสเก็ตช์",
        tool="Close (ปุ่มใน Tasks panel)", target="Tasks panel ▸ Solver messages", value="12 constraints", section=s,
    )

    s = "Pocket: เจาะรูทะลุ"
    guide.step("เลือก HoleSketch แล้วสร้าง Pocket", tool="Part Design ▸ Create a subtractive feature ▸ Pocket", target="Model tree ▸ HoleSketch", section=s)
    guide.step("ตั้ง Type แล้วกด OK", target="Tasks panel ▸ Pocket parameters ▸ Type", value="Through all", section=s)
    guide.step(
        "ตรวจว่า Tip ของ Body คือ Pocket (ไอคอนลูกศรเขียวอยู่ที่ Pocket)", target="Model tree ▸ Body ▸ Pocket",
        note="mounting-plate.v1 ไม่มี fillet/chamfer — ขอบทุกเส้นคม อย่าเพิ่ม Part Design ▸ Fillet/Chamfer ไม่งั้น volume จะไม่ตรง",
        section=s,
    )

    s = "บันทึกและส่งออก"
    guide.step("บันทึกไฟล์ native", tool="File ▸ Save As…", value="part.FCStd", section=s)
    guide.step(
        "ตั้งค่า STEP ก่อน export", tool="Edit ▸ Preferences… ▸ Import-Export ▸ STEP",
        value="Units = Millimeter, Scheme = AP214 International Standard", section=s,
    )
    guide.step("เลือก Pocket แล้ว export STEP", tool="File ▸ Export…", target="Model tree ▸ Pocket",
               value="part.step (STEP with colors (*.step *.stp))", section=s)
    guide.step(
        "เลือก Pocket แล้ว export STL", tool="File ▸ Export…", target="Model tree ▸ Pocket",
        value="part.stl (STL Mesh (*.stl *.ast))",
        note="จำนวนสามเหลี่ยมขึ้นกับ Preferences ▸ Import-Export ▸ Mesh Formats ▸ Maximum mesh deviation จึงอาจไม่เท่ากับของเครื่องมือ — ให้เทียบ volume แทน",
        section=s,
    )
    guide.step(
        "ถ้าต้องการแบบ 2D: เปิด `drawing.svg` / `drawing.dxf` ที่เครื่องมือสร้าง หรือทำเองใน TechDraw",
        tool="TechDraw ▸ Insert Default Page ▸ Insert View", target="Pocket (Top + Right view)",
        value="Scale 1:1, dimensions ตามตาราง 2", section=s,
    )

    volume = f"{props.volume_mm3:.3f}"
    guide.checks = [
        "PlateSketch และ HoleSketch ขึ้น `Fully constrained` และมี constraint อย่างละ 12 ตัว",
        f"Bounding box (Python console: `App.ActiveDocument.Pocket.Shape.BoundBox`) = X 0.000..{w}, Y 0.000..{h}, Z 0.000..{t} (คลาดได้ ≤ 0.01 mm)",
        f"Volume (`App.ActiveDocument.Pocket.Shape.Volume`) ≈ {volume} mm³ = ({w} × {h} − 4·π·({dia}/2)²) × {t}; STEP คลาดได้ ≤ 0.1%",
        "มี solid เดียว และ `Part ▸ Check geometry` ไม่มี error; รูทรงกระบอกทะลุ 4 รูที่ "
        + ", ".join(f"{hole.label} ({mm(hole.x_mm)}, {mm(hole.y_mm)})" for hole in props.hole_centers),
        f"ผนังเหลือ (web) ขอบ X {mm(props.edge_web_x_mm)} / Y {mm(props.edge_web_y_mm)} mm, ระหว่างรู X {mm(props.inner_web_x_mm)} / Y {mm(props.inner_web_y_mm)} mm (ต้อง ≥ 2.000)",
        f"Surface area ≈ {props.surface_area_mm2:.3f} mm² (ใช้ประเมินงานพ่นสี/ชุบ)",
    ]
    if qa is not None:
        guide.checks.append(
            f"ค่าที่เครื่องมือวัดได้จริง: volume {qa.volume_mm3:.3f} mm³, bounds {[round(v, 3) for v in qa.bounds_mm]}, "
            f"bores {qa.bore_count}, STEP rel. error {qa.step_relative_volume_error:.2e}, STL triangles {qa.stl_triangle_count}"
        )
    guide.checks.append(
        "น้ำหนักโดยประมาณ (density ตามตำรา, ไม่ใช่ค่า QA): "
        + ", ".join(f"{name} {props.mass_g(name):.1f} g" for name in MATERIAL_DENSITIES_G_CM3)
    )
    guide.glossary = {
        "Fusion 360": "Create Sketch (XY) ▸ 2-Point Rectangle + Sketch Dimension; Extrude Distance = ความหนา; "
        "Sketch บนผิวบน ▸ Center Diameter Circle ×4; Extrude Operation = Cut, Extent = All",
        "Onshape": "Sketch on Top plane ▸ Corner rectangle + Dimension; Extrude Blind = ความหนา; "
        "Sketch ▸ Circle ×4 + Dimension; Extrude Remove ▸ Through all",
        "SolidWorks": "Sketch on Top Plane ▸ Corner Rectangle + Smart Dimension; Extruded Boss/Base Blind; "
        "Hole Wizard หรือ Extruded Cut ▸ Through All",
        "Constraint": "เงื่อนไขในสเก็ตช์ที่ล็อกตำแหน่ง/ขนาด; Fully constrained = ไม่มีอะไรขยับได้อีก",
    }
    return guide


__all__ = ["FREECAD_VERSION", "build_freecad_guide"]
