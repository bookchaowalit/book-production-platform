"""Manual-reproduction guides for game jobs: build the same scene in the Unity 6 Editor.

The steps mirror ``SceneCompiler.cs`` exactly: one GameObject per entity (no
Tilemap) at ``CellCenter`` = ``(x * tile - (width - 1) * tile / 2, y * tile -
(height - 1) * tile / 2, 0)`` with ``localScale = tile``; SpriteRenderer
sorting order 1 for player/walls and 2 for objectives/exit; player
Rigidbody2D (gravity 0, rotation frozen, continuous) + BoxCollider2D 0.72 +
TopDownPlayerController; wall BoxCollider2D 1; objective CircleCollider2D 0.38
trigger + Collectible; exit BoxCollider2D 0.8 trigger + ExitGate; orthographic
camera size ``max(width, height) * tile * 0.6``; four boundary colliders. An
optional Grid (cell size = tile) is offered only as a snapping aid. Values
come from the validated GameIR; Unity itself is not run by these tests.
"""

from __future__ import annotations

from book_production_platform.game_ir import Cell, GameIR, validate_game_ir
from book_production_platform.game_production.level_map import ascii_map
from book_production_platform.game_production.metrics import LevelMetrics, level_metrics
from book_production_platform.game_production.runtime import EXPECTED_UNITY_VERSION
from book_production_platform.production_trace import ManualGuide

ART_ROOT = "Assets/BookProduction/Art"


def num(value: float) -> str:
    """Short exact decimal for Inspector fields (``-3.5``, ``4.8``, ``1``)."""

    text = f"{round(value, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def world_position(cell: Cell, ir: GameIR) -> tuple[float, float]:
    tile = ir.world.tile_size
    return (
        cell[0] * tile - (ir.world.width - 1) * tile * 0.5,
        cell[1] * tile - (ir.world.height - 1) * tile * 0.5,
    )


def _position(cell: Cell, ir: GameIR) -> str:
    x, y = world_position(cell, ir)
    return f"X {num(x)}, Y {num(y)}, Z 0"


def build_unity_guide(ir: GameIR, metrics: LevelMetrics | None = None) -> ManualGuide:
    """Translate one validated GameIR into numbered Unity 6 Editor steps."""

    level = validate_game_ir(ir)
    stats = metrics or level_metrics(level)
    tile = level.world.tile_size
    width, height = level.world.width, level.world.height
    scale = f"X {num(tile)}, Y {num(tile)}, Z {num(tile)}"
    camera_size = max(width, height) * tile * 0.6
    wall_cells = [(wall.id, cell) for wall in level.walls for cell in wall.cells]

    guide = ManualGuide(
        program="Unity Editor",
        program_version=f"6 ({EXPECTED_UNITY_VERSION})",
        alternatives=["Godot 4.x (TileMapLayer + CharacterBody2D)", "GameMaker"],
        title=f"Top-down collect scene `{level.scene_id}` ({level.template})",
        summary=(
            f"ฉาก 2D มุมบน {width} × {height} ช่อง (ช่องละ {num(tile)} unit): ผู้เล่น 1, กำแพง {len(wall_cells)} ช่อง, "
            f"ของสะสม {len(level.objectives)} ชิ้น, ทางออก 1 — เก็บครบแล้วเดินเข้าทางออกจึงชนะ "
            f"ทำตามนี้จะได้ hierarchy เดียวกับ `Assets/Scenes/{level.scene_id}.unity` ที่เครื่องมือสร้าง"
        ),
        estimated_minutes=round(15 + 0.3 * len(wall_cells) + 1.0 * len(level.objectives), 1),
        setup=[
            f"Unity Hub ▸ `Installs ▸ Install Editor` ▸ เวอร์ชัน `{EXPECTED_UNITY_VERSION}` (ต้องตรง เพราะ fingerprint ผูกกับเวอร์ชัน)",
            "คัดลอกโฟลเดอร์ template `game_production/templates/unity_project` ไปที่ใหม่ ▸ Unity Hub ▸ `Projects ▸ Add ▸ Add project from disk`",
            "เปิดโปรเจกต์ ▸ หน้าต่าง `Project` ▸ `Assets/BookProduction/Runtime` ต้องมี GameProgress, TopDownPlayerController, Collectible, ExitGate, SceneEntityIdentity",
            "แผนผังด่าน (`#` กำแพง, `P` ผู้เล่น, `o` ของสะสม, `E` ทางออก, แถวบน = y สูงสุด):\n```\n" + ascii_map(level) + "\n```",
        ],
    )

    guide.layer("Main Camera", "Camera (Orthographic)", content=f"Size {num(camera_size)}", position="X 0, Y 0, Z -10")
    guide.layer("GameProgress", "GameProgress", content=f"Total Objectives = {len(level.objectives)}", position="root")
    for name in ("Boundary-West", "Boundary-East", "Boundary-South", "Boundary-North"):
        guide.layer(name, "BoxCollider2D", content="ขอบโลก", position="ดูขั้นตอน")
    guide.layer(
        f"Player-{level.player.id}", "SpriteRenderer(player, order 1) + Rigidbody2D + BoxCollider2D + TopDownPlayerController",
        content=f"cell ({level.player.spawn[0]}, {level.player.spawn[1]}), Move Speed {num(level.player.speed)}",
        position=_position(level.player.spawn, level),
    )
    for wall_id, cell in wall_cells:
        guide.layer(
            f"Wall-{wall_id}-{cell[0]}-{cell[1]}", "SpriteRenderer(wall, order 1) + BoxCollider2D",
            content=f"cell ({cell[0]}, {cell[1]})", position=_position(cell, level),
        )
    for objective in level.objectives:
        guide.layer(
            f"Objective-{objective.id}", "SpriteRenderer(objective, order 2) + CircleCollider2D(trigger) + Collectible",
            content=f"cell ({objective.cell[0]}, {objective.cell[1]})", position=_position(objective.cell, level),
        )
    guide.layer(
        f"Exit-{level.exit.id}", "SpriteRenderer(exit, order 2) + BoxCollider2D(trigger) + ExitGate",
        content=f"cell ({level.exit.cell[0]}, {level.exit.cell[1]})", position=_position(level.exit.cell, level),
    )

    s = "ฉากและ sprite"
    guide.step("สร้างฉากเปล่า", tool="File ▸ New Scene ▸ Empty (Built-in) ▸ Create", section=s)
    guide.step("บันทึกฉาก", tool="File ▸ Save As…", value=f"Assets/Scenes/{level.scene_id}.unity", section=s)
    for sprite in ("player", "wall", "objective", "exit"):
        guide.step(
            f"ตั้งค่า import ของ {sprite}.png แล้วกด Apply", target=f"Project ▸ {ART_ROOT}/{sprite}.png ▸ Inspector",
            value="Texture Type = Sprite (2D and UI), Pixels Per Unit = 16, Filter Mode = Point (no filter), Generate Mip Maps = off",
            section=s,
        )

    s = "กล้องและ GameProgress"
    guide.step("สร้างกล้อง", tool="GameObject ▸ Camera", target="Hierarchy", value="Main Camera (Tag = MainCamera)", section=s)
    guide.step("ตั้งตำแหน่งกล้อง", target="Inspector ▸ Transform ▸ Position", value="X 0, Y 0, Z -10", section=s)
    guide.step("ตั้งเป็นกล้อง 2D", target="Inspector ▸ Camera ▸ Projection", value="Orthographic", section=s)
    guide.step("ตั้งขนาดภาพ = max(width, height) × tile × 0.6", target="Inspector ▸ Camera ▸ Size", value=num(camera_size), section=s)
    guide.step(
        "ตั้งพื้นหลัง", target="Inspector ▸ Camera ▸ Environment ▸ Background Type / Background",
        value="Solid Color, RGBA (0.055, 0.075, 0.11, 1) = #0E131C", section=s,
    )
    guide.step("สร้าง GameObject เปล่าชื่อ GameProgress", tool="GameObject ▸ Create Empty", target="Hierarchy (root)", value="GameProgress", section=s)
    guide.step("เพิ่มสคริปต์", tool="Inspector ▸ Add Component ▸ GameProgress", target="GameProgress", section=s)
    guide.step("ตั้งจำนวนของที่ต้องเก็บ", target="Inspector ▸ GameProgress ▸ Total Objectives", value=str(len(level.objectives)), section=s)
    if level.audio is not None and (level.audio.music or level.audio.collect_sfx):
        assets = {asset.asset_id: asset for asset in level.assets}
        if level.audio.collect_sfx:
            clip = assets[level.audio.collect_sfx]
            guide.step(
                "เพิ่มเสียงตอนเก็บของ", tool="Inspector ▸ Add Component ▸ Audio Source", target="GameProgress",
                value=f"AudioClip = Assets/BookProduction/Audio/{clip.asset_id}{clip.filename[clip.filename.rfind('.'):]}, Play On Awake = off, Loop = off, Spatial Blend = 0",
                note="ลาก Audio Source นี้ไปใส่ช่อง Collect Sound ของ GameProgress (Inspector debug mode)", section=s,
            )
        if level.audio.music:
            clip = assets[level.audio.music]
            guide.step(
                "สร้างเพลงประกอบ", tool="GameObject ▸ Create Empty ▸ Add Component ▸ Audio Source", target="Hierarchy (root)",
                value=f"ชื่อ Music, AudioClip = Assets/BookProduction/Audio/{clip.asset_id}{clip.filename[clip.filename.rfind('.'):]}, Play On Awake = on, Loop = on, Spatial Blend = 0",
                section=s,
            )
    else:
        guide.step(
            "ลบ Audio Listener ออกจากกล้อง (ฉากนี้ไม่มีเสียง)", tool="Inspector ▸ Audio Listener ▸ ⋮ ▸ Remove Component",
            target="Main Camera", section=s,
        )

    s = "ขอบโลก (Boundary)"
    half_w, half_h = width * tile * 0.5, height * tile * 0.5
    bounds = (
        ("Boundary-West", (-half_w - tile * 0.5, 0.0), (tile, height * tile + tile * 2)),
        ("Boundary-East", (half_w + tile * 0.5, 0.0), (tile, height * tile + tile * 2)),
        ("Boundary-South", (0.0, -half_h - tile * 0.5), (width * tile, tile)),
        ("Boundary-North", (0.0, half_h + tile * 0.5), (width * tile, tile)),
    )
    for name, (x, y), (w, h) in bounds:
        guide.step(
            f"สร้าง {name} แล้ว Add Component ▸ Box Collider 2D", tool="GameObject ▸ Create Empty", target="Hierarchy (root)",
            value=f"Position X {num(x)}, Y {num(y)}, Z 0; Box Collider 2D Size X {num(w)}, Y {num(h)}", section=s,
        )

    s = "Grid ช่วยจัดตำแหน่ง (ไม่บังคับ)"
    guide.step(
        "สร้าง Grid เพื่อ snap (เครื่องมือไม่ได้ใช้ Tilemap — วางเป็น GameObject แยกทีละชิ้น)",
        tool="GameObject ▸ 2D Object ▸ Tilemap ▸ Rectangular", target="Hierarchy",
        value=f"Grid ▸ Cell Size X {num(tile)}, Y {num(tile)}, Z 0; Grid Position X {num(-half_w)}, Y {num(-half_h)}, Z 0",
        note="กึ่งกลางช่อง (x, y) ของ Grid นี้ = ตำแหน่ง Position ในตาราง 2 พอดี; ลบ Grid ทิ้งก่อนบันทึกถ้าต้องการ hierarchy ให้ตรงกับเครื่องมือ",
        section=s,
    )

    s = "ผู้เล่น"
    player_name = f"Player-{level.player.id}"
    guide.step("สร้าง sprite ผู้เล่น", tool="GameObject ▸ 2D Object ▸ Sprites ▸ Square", target="Hierarchy (root)", value=player_name, section=s)
    guide.step("ตั้งตำแหน่ง/ขนาด", target="Inspector ▸ Transform", value=f"Position {_position(level.player.spawn, level)}; Scale {scale}", note=f"cell ({level.player.spawn[0]}, {level.player.spawn[1]})", section=s)
    guide.step("ตั้ง sprite และลำดับวาด", target="Inspector ▸ Sprite Renderer", value=f"Sprite = {ART_ROOT}/player.png, Order in Layer = 1", section=s)
    guide.step("เพิ่ม Rigidbody 2D", tool="Inspector ▸ Add Component ▸ Rigidbody 2D", target=player_name,
               value="Gravity Scale = 0, Collision Detection = Continuous, Constraints ▸ Freeze Rotation Z = on", section=s)
    guide.step("เพิ่ม Box Collider 2D", tool="Inspector ▸ Add Component ▸ Box Collider 2D", target=player_name, value="Size X 0.72, Y 0.72", section=s)
    guide.step("เพิ่มสคริปต์ควบคุม", tool="Inspector ▸ Add Component ▸ TopDownPlayerController", target=player_name,
               value=f"Move Speed = {num(level.player.speed)}", note="บังคับด้วย WASD หรือปุ่มลูกศร", section=s)
    guide.step("เพิ่ม identity", tool="Inspector ▸ Add Component ▸ SceneEntityIdentity", target=player_name,
               value=f"Entity Id = {level.player.id}, Entity Type = player", section=s)

    s = "กำแพง"
    if wall_cells:
        first_id, first_cell = wall_cells[0]
        first_name = f"Wall-{first_id}-{first_cell[0]}-{first_cell[1]}"
        guide.step("สร้างกำแพงชิ้นแรก", tool="GameObject ▸ 2D Object ▸ Sprites ▸ Square", target="Hierarchy (root)", value=first_name, section=s)
        guide.step("ตั้ง sprite/collider/identity ของกำแพง", target=f"Inspector ▸ {first_name}",
                   value=f"Sprite = {ART_ROOT}/wall.png, Order in Layer = 1; Add Component ▸ Box Collider 2D Size X 1, Y 1; "
                         f"SceneEntityIdentity Entity Type = wall; Scale {scale}", section=s)
        for wall_id, cell in wall_cells:
            name = f"Wall-{wall_id}-{cell[0]}-{cell[1]}"
            guide.step(
                f"วางกำแพงที่ cell ({cell[0]}, {cell[1]})" + ("" if (wall_id, cell) == wall_cells[0] else " — Ctrl+D ทำสำเนาชิ้นก่อนแล้วเปลี่ยนชื่อ"),
                target=f"Hierarchy ▸ {name} ▸ Transform ▸ Position",
                value=f"{_position(cell, level)}; Entity Id = wall:{wall_id}:{cell[0]}:{cell[1]}", section=s,
            )

    s = "ของสะสม"
    for index, objective in enumerate(level.objectives):
        name = f"Objective-{objective.id}"
        if index == 0:
            guide.step("สร้างของสะสมชิ้นแรก", tool="GameObject ▸ 2D Object ▸ Sprites ▸ Circle", target="Hierarchy (root)", value=name, section=s)
            guide.step("ตั้ง sprite/collider/script", target=f"Inspector ▸ {name}",
                       value=f"Sprite = {ART_ROOT}/objective.png, Order in Layer = 2; Add Component ▸ Circle Collider 2D Radius 0.38, Is Trigger = on; "
                             f"Add Component ▸ Collectible (Progress = GameProgress); SceneEntityIdentity Entity Type = objective; Scale {scale}", section=s)
        guide.step(
            f"วาง {objective.id} ที่ cell ({objective.cell[0]}, {objective.cell[1]})" + ("" if index == 0 else " — Ctrl+D แล้วเปลี่ยนชื่อ"),
            target=f"Hierarchy ▸ {name}",
            value=f"Position {_position(objective.cell, level)}; Collectible ▸ Objective Id = {objective.id}; Entity Id = {objective.id}",
            section=s,
        )

    s = "ทางออก"
    exit_name = f"Exit-{level.exit.id}"
    guide.step("สร้างทางออก", tool="GameObject ▸ 2D Object ▸ Sprites ▸ Square", target="Hierarchy (root)", value=exit_name, section=s)
    guide.step("ตั้งค่า", target=f"Inspector ▸ {exit_name}",
               value=f"Position {_position(level.exit.cell, level)}; Scale {scale}; Sprite = {ART_ROOT}/exit.png, Order in Layer = 2; "
                     f"Box Collider 2D Size X 0.8, Y 0.8, Is Trigger = on; ExitGate ▸ Exit Id = {level.exit.id}, Progress = GameProgress; "
                     f"Entity Id = {level.exit.id}, Entity Type = exit", section=s)

    s = "ทดสอบ Play mode และ build"
    guide.step("บันทึกฉาก", tool="File ▸ Save", value=f"Assets/Scenes/{level.scene_id}.unity", section=s)
    guide.step("ใส่ฉากใน build", tool="File ▸ Build Profiles ▸ Scene List ▸ Add Open Scenes", value=f"Assets/Scenes/{level.scene_id}.unity", section=s)
    guide.step("กด Play แล้วเดินไปที่ทางออกก่อนเก็บของ — ต้องยังไม่ชนะ", tool="Play (Ctrl+P)", target="Game view",
               value=f"event exit_blocked:{level.exit.id}", section=s)
    for index, objective_id in enumerate(stats.route_order, start=1):
        objective = next(item for item in level.objectives if item.id == objective_id)
        guide.step(
            f"เก็บชิ้นที่ {index} ตามเส้นทางสั้นสุด", target=f"cell ({objective.cell[0]}, {objective.cell[1]})",
            value=f"event objective_collected:{objective_id}", section=s,
        )
    guide.step(
        "เดินเข้าทางออก — GameProgress ▸ Is Won ต้องเป็น on", target=f"cell ({level.exit.cell[0]}, {level.exit.cell[1]})",
        value=f"event game_won:{level.exit.id}", section=s,
    )

    guide.checks = [
        f"Hierarchy มี Player 1, Wall {len(wall_cells)}, Objective {len(level.objectives)}, Exit 1, Boundary 4, Main Camera, GameProgress",
        f"ทุกของสะสมและทางออกเดินถึงได้จากจุดเกิด (เครื่องมือตรวจ BFS 4 ทิศ: เข้าถึงได้ {stats.reachable_cells}/{stats.walkable_cells} ช่องว่าง)",
        (
            f"เส้นทางสั้นสุด ({stats.route_method}) = {stats.route_steps} ช่อง ลำดับ {' → '.join(stats.route_order)} → {level.exit.id}; "
            f"เดินตามกริดใช้ ~{stats.estimated_seconds} วินาที ที่ Move Speed {num(level.player.speed)}"
        ),
        f"ระดับความยาก (heuristic {stats.profile}) = {stats.difficulty_band} (score {stats.difficulty_score}); dead ends {stats.dead_ends}, junctions {stats.junctions}",
        "Console ไม่มี error และ Play mode จบด้วย event game_won (เทียบกับ `gameplay-trace.json` ของเครื่องมือ)",
        "ไม่มี component อื่นนอกจากที่ระบุ และไม่มี asset จากภายนอกโปรเจกต์",
    ]
    guide.glossary = {
        "CellCenter": "ตำแหน่งกลางช่อง = (x × tile − (width − 1) × tile / 2, y × tile − (height − 1) × tile / 2)",
        "Godot 4": "TileMapLayer (tile size = tile) สำหรับกำแพง หรือ StaticBody2D ต่อช่อง; ผู้เล่น CharacterBody2D; ของสะสม/ทางออก Area2D",
        "Order in Layer": "ลำดับวาดของ SpriteRenderer; เลขมากวาดทับ",
    }
    return guide


def build_export_guide(target: str, product_name: str, unity_target: str) -> ManualGuide:
    """Unity Build Profiles steps that reproduce ``export_game_player`` for one platform."""

    guide = ManualGuide(
        program="Unity Editor",
        program_version=f"6 ({EXPECTED_UNITY_VERSION})",
        alternatives=["Godot 4.x (Project ▸ Export)"],
        title=f"Player build `{product_name}` → {target}",
        summary="สร้างไฟล์เล่นได้จากโปรเจกต์ที่เครื่องมือรับรองแล้ว (accepted) โดยไม่แก้ฉาก",
        estimated_minutes=10,
        setup=[
            "เปิดโฟลเดอร์ `project/` ของงานที่ accepted ใน Unity Hub (Add project from disk)",
            f"ติดตั้ง build support ของแพลตฟอร์ม: Unity Hub ▸ Installs ▸ {EXPECTED_UNITY_VERSION} ▸ Add modules ▸ "
            + ("Web Build Support" if target == "web" else "Windows Build Support (IL2CPP/Mono)"),
        ],
    )
    guide.layer("player/", unity_target, content="ไฟล์ที่ build ได้", position="โฟลเดอร์ปลายทาง")
    guide.step("เปิด Build Profiles", tool="File ▸ Build Profiles", section="Build")
    guide.step("เลือกแพลตฟอร์มแล้ว Switch Platform", target="Platforms", value=unity_target, section="Build")
    guide.step("ตรวจว่ามีฉากเดียวใน Scene List", target="Scene List", value=f"Assets/Scenes/{product_name}.unity", section="Build")
    guide.step("ตั้งชื่อผลิตภัณฑ์", tool="Edit ▸ Project Settings ▸ Player ▸ Product Name", value=product_name, section="Build")
    guide.step("กด Build แล้วเลือกโฟลเดอร์ปลายทาง", tool="Build", value="player/", section="Build")
    guide.checks = [
        "Build ไม่มี error",
        "web: มี index.html + Build/*.loader.js/.data/.framework.js/.wasm; windows: มี .exe + UnityPlayer.dll + *_Data/",
        "ลบโฟลเดอร์ *_BurstDebugInformation_DoNotShip และ *_BackUpThisFolder_ButDontShipItWithYourGame ก่อนส่ง",
    ]
    return guide


__all__ = ["build_export_guide", "build_unity_guide", "num", "world_position"]
