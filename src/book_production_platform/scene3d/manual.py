"""Manual-reproduction guide for Scene3D: the same studio scene by hand in Blender.

The steps mirror :mod:`scene3d.scene_builder` exactly and read every number
from its preset tables (world colour, ground plane, area lights, camera lens,
fit margin, render engine, samples, resolution, PNG settings), so a person
following them in the pinned Blender build rebuilds ``scene.blend`` and renders
``render.png``. Rotations are given two ways: a ``Track To`` constraint (the
same ``-Z``/``Y`` tracking the engine uses) and the equivalent Euler angles.

Values that depend on the imported product (fit scale, Product Root offset,
and the camera distance of ``*-fit`` compositions) are measured inside Blender
by the engine; the guide gives the engine's formula and says to copy the
values from ``scene.blend`` rather than estimate them.
"""

from __future__ import annotations

import math

from book_production_platform.production_trace import ManualGuide

from .models import SceneIR
from .scene_builder import (
    _CAMERA_OFFSET,
    _FIT_MARGIN,
    _LEGACY_TARGET,
    BACKGROUND_PRESETS,
    CAMERA_LENS_MM,
    CAMERA_SENSOR_WIDTH_MM,
    EEVEE_SAMPLES,
    FIT_DIRECTIONS,
    LIGHT_TARGET,
    LIGHTING_PRESETS,
    PNG_COMPRESSION,
    RENDER_PROFILES,
    VIEW_TRANSFORM,
)

BLENDER_VERSION = "5.2.2 LTS"
Vec3 = tuple[float, float, float]


def _fmt(values: tuple[float, ...], digits: int = 4) -> str:
    return ", ".join(f"{round(value, digits):g}" for value in values)


def track_rotation_degrees(direction: Vec3) -> Vec3:
    """Euler XYZ (degrees) of ``direction.to_track_quat('-Z', 'Y')`` for a non-vertical direction.

    The tracked ``-Z`` axis points along ``direction`` with ``Y`` kept up and no
    roll, which gives ``X = acos(-dz)``, ``Y = 0``, ``Z = atan2(-dx, dy)``.
    """

    length = math.sqrt(sum(component * component for component in direction))
    dx, dy, dz = (component / length for component in direction)
    return (round(math.degrees(math.acos(-dz)), 2), 0.0, round(math.degrees(math.atan2(-dx, dy)), 2))


def aim_rotation(location: Vec3, target: Vec3) -> Vec3:
    return track_rotation_degrees(tuple(t - loc for t, loc in zip(target, location, strict=True)))  # type: ignore[arg-type]


def fit_distance_factor(render_profile: str) -> float:
    """Camera distance per unit of product bounding-sphere radius (``_fit_camera``)."""

    width, height = RENDER_PROFILES[render_profile]
    horizontal = 2.0 * math.atan(CAMERA_SENSOR_WIDTH_MM * 0.5 / CAMERA_LENS_MM)
    vertical = 2.0 * math.atan(math.tan(horizontal * 0.5) * height / width) if width >= height else horizontal
    return _FIT_MARGIN / math.sin(min(horizontal, vertical) * 0.5)


def build_blender_guide(scene: SceneIR, *, mesh_objects: int | None = None) -> ManualGuide:
    """Translate a validated SceneIR into numbered Blender steps and an object table."""

    width, height = RENDER_PROFILES[scene.render_profile]
    background = BACKGROUND_PRESETS[scene.background]
    lights = LIGHTING_PRESETS[scene.lighting]
    transparent = bool(background["film_transparent"])
    ground = background["ground"]
    asset_name = scene.asset.path.split("/")[-1]
    guide = ManualGuide(
        program="Blender",
        program_version=BLENDER_VERSION,
        alternatives=["KeyShot", "Cinema 4D + Redshift"],
        title=f"Product hero `{asset_name}` · {scene.composition} · {scene.lighting} · {scene.background} · {width}×{height}",
        summary=(
            "ทำตามขั้นตอนนี้ใน Blender รุ่นเดียวกับเครื่องมือ จะได้ฉาก studio เดียวกับ `scene.blend` และภาพ `render.png`; "
            "ตัวเลขทุกตัวมาจาก preset ของเครื่องมือ ส่วนค่าที่ขึ้นกับขนาดสินค้าให้คัดลอกจาก `scene.blend`"
        ),
        estimated_minutes=float(15 + 3 * len(lights) + (3 if ground else 0)),
        setup=[
            f"เปิด Blender {BLENDER_VERSION} ▸ `File ▸ New ▸ General`",
            "ลบวัตถุเริ่มต้นทั้งหมด (เครื่องมือเริ่มจากฉากว่าง): `Select ▸ All` (A) แล้ว `Object ▸ Delete` (X)",
            "`Scene Properties ▸ Units ▸ Unit System` = None",
            "KeyShot: import GLB แล้วตั้ง Camera/Lighting/Environment ตามตารางเดียวกัน (ค่า Energy ของ Blender ไม่เท่ากับหน่วยแสงของ KeyShot)",
        ],
        glossary={
            "Product Root": "Empty ที่เป็น parent ของ mesh สินค้า ใช้ย่อ/ขยายให้ด้านยาวสุด = 2.0 และวางฐานบนพื้น",
            "Track To": "constraint ที่หมุนวัตถุให้แกน -Z ชี้ไปที่เป้า (แบบเดียวกับที่เครื่องมือคำนวณ)",
        },
    )

    guide.layer(asset_name, "Imported mesh objects", content=f"{mesh_objects} mesh" if mesh_objects is not None else "ตาม GLB", position="parent = Product Root")
    guide.layer("Product Root", "Empty (Plain Axes)", content="scale = 2.0 / ด้านยาวสุด", position="ฐานสินค้าอยู่ที่ Z=0, กึ่งกลาง X/Y ที่ 0")
    if ground:
        guide.layer("Studio Ground", "Mesh Plane", content=f"size {ground['size']:g} · Base Color {_fmt(ground['base_color'])} · Roughness {ground['roughness']:g}", position=f"location {_fmt(ground['location'])}")
    guide.layer("Product Hero Camera", "Camera", content=f"lens {CAMERA_LENS_MM:g} mm · DOF off", position=scene.composition)
    for name, location, energy, size, color in lights:
        guide.layer(name, "Light (Area, Disk)", content=f"{energy:g} W · size {size:g} m · color {_fmt(color)}", position=f"location {_fmt(location)}")
    guide.layer("World", "World", content=f"Color {_fmt(background['world_color'])} · Strength {background['world_strength']:g}", position="—")

    section = "นำเข้าสินค้า"
    guide.step("นำเข้าไฟล์ GLB (ค่า import เริ่มต้น)", tool="File ▸ Import ▸ glTF 2.0 (.glb/.gltf)", value=asset_name, section=section)
    guide.step("สร้าง Empty สำหรับรวมสินค้าที่ origin", tool="Add ▸ Empty ▸ Plain Axes", target="3D Viewport, 3D cursor ที่ 0,0,0", section=section)
    guide.step("ตั้งชื่อ Empty", tool="Outliner ▸ ดับเบิลคลิกชื่อ", value="Product Root", section=section)
    guide.step(
        "เลือก mesh ที่ import ทั้งหมด แล้ว Shift+คลิก Product Root เป็น active จากนั้นตั้ง parent",
        tool="Object ▸ Parent ▸ Object (Keep Transform) (Ctrl+P)",
        target="Outliner",
        section=section,
    )
    guide.step(
        "ตั้งขนาด Product Root ให้ด้านยาวสุดของสินค้า = 2.0",
        tool="Object Properties ▸ Transform ▸ Scale",
        value="s = 2.0 / max(Dimensions X, Y, Z) ของกล่องรวม mesh ทุกชิ้น (ใส่ s ทั้ง 3 แกน)",
        note="ค่านี้ขึ้นกับสินค้า: คัดลอก Scale ของ `Product Root` จาก `scene.blend` ของเครื่องมือเพื่อให้ตรงทุกหลัก",
        section=section,
    )
    guide.step(
        "วางสินค้าให้ฐานแตะพื้นและอยู่กึ่งกลาง",
        tool="Object Properties ▸ Transform ▸ Location",
        value="(−center_x × s, −center_y × s, −min_z × s) ของกล่องรวมก่อนย่อ",
        note="คัดลอก Location ของ `Product Root` จาก `scene.blend` ได้เช่นกัน",
        section=section,
    )
    guide.step("วัสดุ: ใช้วัสดุจาก GLB ตามเดิม ไม่ต้องแก้ (preset `preserve-source.v1`)", target="Material Properties", value=scene.material_preset, section=section)

    section = f"World และพื้น ({scene.background})"
    guide.step("สร้าง World ใหม่ (Use Nodes)", tool="World Properties ▸ New", section=section)
    guide.step("ตั้งสีพื้นหลังของ World", tool="World Properties ▸ Surface ▸ Background ▸ Color (RGBA)", value=_fmt(background["world_color"]), section=section)
    guide.step("ตั้งความแรงแสงของ World", tool="World Properties ▸ Surface ▸ Strength", value=f"{background['world_strength']:g}", section=section)
    if ground:
        guide.step("เพิ่มพื้น", tool="Add ▸ Mesh ▸ Plane", section=section)
        guide.step("ตั้งขนาดและตำแหน่งพื้นใน Adjust Last Operation", tool="Adjust Last Operation ▸ Size / Location", value=f"Size = {ground['size']:g} m, Location = {_fmt(ground['location'])}", section=section)
        guide.step("ตั้งชื่อ", tool="Outliner ▸ ดับเบิลคลิกชื่อ", value="Studio Ground", section=section)
        guide.step("เพิ่มวัสดุ Principled BSDF", tool="Material Properties ▸ New", value="Studio Neutral Ground", section=section)
        guide.step("ตั้งสีพื้น", tool="Material Properties ▸ Surface ▸ Base Color (RGBA)", value=_fmt(ground["base_color"]), section=section)
        guide.step("ตั้งความหยาบ", tool="Material Properties ▸ Surface ▸ Roughness", value=f"{ground['roughness']:g}", section=section)
    else:
        guide.step("ไม่ต้องสร้างพื้น (พื้นหลังโปร่งใส)", target="—", section=section)

    section = f"กล้อง ({scene.composition})"
    guide.step("เพิ่มกล้อง", tool="Add ▸ Camera", section=section)
    guide.step("ตั้งชื่อ", tool="Outliner ▸ ดับเบิลคลิกชื่อ", value="Product Hero Camera", section=section)
    guide.step("ตั้งเลนส์", tool="Camera Properties ▸ Lens ▸ Focal Length", value=f"{CAMERA_LENS_MM:g} mm", section=section)
    guide.step("ตั้ง sensor (ค่าเริ่มต้น)", tool="Camera Properties ▸ Camera ▸ Sensor Fit / Size", value=f"Auto, {CAMERA_SENSOR_WIDTH_MM:g} mm", section=section)
    guide.step("ปิด depth of field", tool="Camera Properties ▸ Depth of Field", value="off", section=section)
    if scene.composition in FIT_DIRECTIONS:
        view = FIT_DIRECTIONS[scene.composition]
        factor = fit_distance_factor(scene.render_profile)
        rotation = track_rotation_degrees(tuple(-component for component in view))  # type: ignore[arg-type]
        guide.step(
            "วางกล้องตามสูตร fit ของเครื่องมือ",
            tool="Object Properties ▸ Transform ▸ Location",
            value=(
                f"target = (0, 0, size_z / 2); distance = (|size| / 2) × {factor:.4f}; "
                f"location = target + normalize({_fmt(view, 6)}) × distance"
            ),
            note=(
                f"size = ขนาดสินค้าหลังย่อ (ด้านยาวสุด 2.0); {factor:.4f} = {_FIT_MARGIN:g} / sin(ครึ่งมุมมองที่แคบกว่า) "
                f"ของเลนส์ {CAMERA_LENS_MM:g} mm ที่ {width}×{height}; ค่าขึ้นกับสินค้า — คัดลอก Location กล้องจาก `scene.blend` เพื่อให้ตรงทุกหลัก"
            ),
            section=section,
        )
        guide.step(
            "หมุนกล้องให้มองตามทิศ composition (ไม่ขึ้นกับขนาดสินค้า)",
            tool="Object Properties ▸ Transform ▸ Rotation (XYZ Euler)",
            value=f"X={rotation[0]:g}°, Y={rotation[1]:g}°, Z={rotation[2]:g}°",
            note="หรือเพิ่ม Empty `Camera Target` ที่ target แล้วใช้ `Object Constraint ▸ Track To` (Track Axis -Z, Up Axis Y)",
            section=section,
        )
    else:
        rotation = aim_rotation(_CAMERA_OFFSET, _LEGACY_TARGET)
        guide.step("ตั้งตำแหน่งกล้อง", tool="Object Properties ▸ Transform ▸ Location", value=_fmt(_CAMERA_OFFSET), section=section)
        guide.step(
            "หมุนกล้องให้มองเป้า",
            tool="Object Properties ▸ Transform ▸ Rotation (XYZ Euler)",
            value=f"X={rotation[0]:g}°, Y={rotation[1]:g}°, Z={rotation[2]:g}°",
            note=f"เท่ากับ Track To ไปที่ Empty ที่ {_fmt(_LEGACY_TARGET)} (Track Axis -Z, Up Axis Y)",
            section=section,
        )
    guide.step("ตั้งเป็นกล้องหลักของฉาก", tool="View ▸ Cameras ▸ Set Active Object as Camera (Ctrl+Numpad 0)", section=section)

    section = f"แสง ({scene.lighting})"
    for name, location, energy, size, color in lights:
        rotation = aim_rotation(location, LIGHT_TARGET)
        guide.step(f"เพิ่มไฟ `{name}`", tool="Add ▸ Light ▸ Area", section=section)
        guide.step("ตั้งชื่อ", tool="Outliner ▸ ดับเบิลคลิกชื่อ", value=name, section=section)
        guide.step("ตั้งตำแหน่ง", tool="Object Properties ▸ Transform ▸ Location", value=_fmt(location), section=section)
        guide.step(
            "หมุนไฟให้ชี้สินค้า",
            tool="Object Properties ▸ Transform ▸ Rotation (XYZ Euler)",
            value=f"X={rotation[0]:g}°, Y={rotation[1]:g}°, Z={rotation[2]:g}°",
            note=f"เท่ากับ Track To ไปที่ {_fmt(LIGHT_TARGET)} (Track Axis -Z, Up Axis Y)",
            section=section,
        )
        guide.step("ตั้งพลังงาน รูปทรง และขนาด", tool="Light Properties ▸ Power / Shape / Size", value=f"Power = {energy:g} W, Shape = Disk, Size = {size:g} m", section=section)
        guide.step("ตั้งสีไฟ", tool="Light Properties ▸ Color (RGB)", value=_fmt(color), section=section)

    section = "Render และ Output"
    guide.step("ตั้ง render engine", tool="Render Properties ▸ Render Engine", value="EEVEE", section=section)
    guide.step("ตั้งจำนวน sample", tool="Render Properties ▸ Sampling ▸ Render", value=str(EEVEE_SAMPLES), section=section)
    guide.step("ตั้ง view transform", tool="Render Properties ▸ Color Management ▸ View Transform", value=VIEW_TRANSFORM, section=section)
    guide.step("ตั้งพื้นหลังโปร่งใสของ film", tool="Render Properties ▸ Film ▸ Transparent", value="on" if transparent else "off", section=section)
    guide.step("ตั้งความละเอียด", tool="Output Properties ▸ Format ▸ Resolution X / Y / %", value=f"{width} × {height}, 100%", section=section)
    guide.step(
        "ตั้งไฟล์ภาพ",
        tool="Output Properties ▸ Output ▸ File Format / Color / Color Depth / Compression",
        value=f"PNG, {'RGBA' if transparent else 'RGB'}, 8, {PNG_COMPRESSION}%",
        section=section,
    )
    guide.step("ฝัง texture ไว้ในไฟล์", tool="File ▸ External Data ▸ Pack Resources", section=section)
    guide.step("บันทึกฉาก", tool="File ▸ Save As… (Shift+Ctrl+S)", value="scene.blend", section=section)
    guide.step("เรนเดอร์ภาพนิ่ง", tool="Render ▸ Render Image (F12)", section=section)
    guide.step("บันทึกภาพ", tool="Image ▸ Save As… (Shift+Alt+S)", value="render.png", section=section)

    guide.checks = [
        f"`render.png` ขนาด {width}×{height} px, PNG {'RGBA' if transparent else 'RGB'} 8-bit",
        f"Outliner มี Product Root, {'Studio Ground, ' if ground else ''}Product Hero Camera และไฟ {len(lights)} ดวง: {', '.join(name for name, *_ in lights)}",
        f"Render Engine = EEVEE, Samples = {EEVEE_SAMPLES}, View Transform = {VIEW_TRANSFORM}, Film Transparent = {'on' if transparent else 'off'}",
        "เปิด `scene.blend` ของเครื่องมือคู่กัน แล้วเทียบ Location/Rotation/Scale ของ Product Root และกล้อง",
        "วาง `render.png` ของเครื่องมือทับภาพที่เรนเดอร์เอง (opacity 50%) แล้วขอบสินค้าต้องซ้อนกันพอดี",
    ]
    return guide


__all__ = ["BLENDER_VERSION", "aim_rotation", "build_blender_guide", "fit_distance_factor", "track_rotation_degrees"]
