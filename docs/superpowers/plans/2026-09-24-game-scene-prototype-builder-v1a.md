# Game Scene Prototype Builder V1A Implementation Plan

> **สำหรับผู้ลงมือ:** ใช้ `superpowers:executing-plans` ทำตาม checkbox ทีละข้อใน checkout `main`; ทดสอบและ commit เฉพาะไฟล์ของ task นั้น

**Goal:** สร้าง Unity project ต้นแบบเกม top-down 2D หนึ่งฉากจาก GameIR ที่ตรวจสอบเข้มงวด เล่นผ่าน collection loop ได้ และมีหลักฐาน QA/replay ที่สร้างซ้ำได้

**Architecture:** Python package ใน `book-production-platform` รับและตรวจ GameIR, canonicalize input, คุม staging/replay และเรียก Unity Editor ด้วย argument array ส่วน Unity project template ที่ติดมากับ package สร้าง scene จาก primitive assets ที่อนุมัติ, รัน EditMode/PlayMode tests และเขียน gameplay trace ให้ Python ตรวจ ก่อน publish จะเปิด scene ใหม่ใน Unity process อีกครั้งและตรวจ hash ของ artifacts

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, Unity Editor 6000.6.2f1, Unity Test Framework 1.8.0 ที่มากับ installation, C# runtime/Editor scripts, NUnit ใน Unity tests

**Spec:** `docs/superpowers/specs/2026-09-24-game-scene-prototype-builder-design.md`

## Global Constraints

- รับ JSON ที่มีขนาดไม่เกิน 256 KiB; ปฏิเสธ NaN, Infinity, field ที่ไม่รู้จัก และ template/behavior ที่ไม่ได้ลงทะเบียน
- World มีขนาดไม่เกิน 64 × 64 cells, entities รวมไม่เกิน 256, static wall cells ไม่เกิน 192 และ objectives ไม่เกิน 32
- Unity worker ใช้ deadline รวมไม่เกิน 120 วินาทีต่องาน รวม build, EditMode, PlayMode และ reopen verification
- ผลลัพธ์ที่ publish มี Unity project หนึ่งชุด, scene หนึ่งไฟล์ และ QA manifest; receipt อยู่ใน fingerprint artifact เดียวกันเพื่อให้ atomic directory rename เผยแพร่ทั้งชุดพร้อมกัน
- Unity version สำหรับ local pilot นี้ต้องเป็น 6000.6.2f1; manifest ระบุ SHA-256 ของ Unity executable เพื่อแยก Editor build
- Test Framework ใช้เวอร์ชัน 1.8.0 จาก BuiltInPackages ของ Unity installation นี้; ห้ามดาวน์โหลด Editor, package หรือ asset ระหว่าง build
- input รับเฉพาะ GameIR ที่ validate แล้ว; ห้ามผู้ใช้ส่ง C#, prefab, shader, path, URL, assembly หรือ Unity type
- เส้นทางปกติเป็น deterministic, ไม่มี LLM/vision/generative asset/remote call และต้องรายงาน `model_calls: 0`; monetary cost เป็น `unknown`
- เผยแพร่จาก staging ใน filesystem เดียวกันหลัง QA ผ่านครบ; replay ต้องตรวจ receipt และ artifact hashes ก่อนตอบผล
- ใช้ static scene order, stable asset GUIDs และไม่มี Library/Temp/UserSettings ใน output hash; ห้ามเขียน output ทับงานเดิมโดยอัตโนมัติ
- Pilot implementation guardrails: Unity log ไม่เกิน 8 MiB, staging project ไม่เกิน 1 GiB และ published project ไม่เกิน 64 MiB; เกิน cap แล้วต้องหยุดและไม่ publish
- local pilot นี้รองรับเฉพาะ input ที่ตรวจรูปแบบและขนาดแล้ว; ยังไม่ใช่ OS sandbox สำหรับ project หรือ asset จากผู้ใช้ที่ไม่เชื่อถือ

## Review Focus

1. malformed JSON, payload เกิน 256 KiB, NaN/Infinity, unknown fields และ version/template ที่ไม่รองรับ ต้องได้ stable rejection ก่อนเรียก Unity; pin ใน `tests/game_ir/test_models.py`
2. code-like fields, path traversal, URL, absolute path และ IDs ที่จะกลายเป็นชื่อไฟล์ ต้องถูกปฏิเสธหรือ escape อย่างปลอดภัย; pin ใน `tests/game_ir/test_validation.py`
3. duplicate IDs, ตำแหน่งชนกัน, coordinate นอกกริด และ objective/exit ที่เดินไปไม่ถึง ต้องไม่ผ่าน validation; pin ใน `tests/game_ir/test_validation.py`
4. manifest, scene, source IR หรือ receipt ถูกแก้หลัง publish ต้องได้ `replay_integrity_error` และห้าม Unity rerun/overwrite; pin ใน `tests/game_production/test_jobs.py`
5. Unity exit code เป็นศูนย์แต่ไม่มี test XML, มี failed/not-run tests, เกิน deadline, log/staging/output เกิน cap ต้องไม่ผ่าน QA; pin ใน `tests/game_production/test_runtime.py` และ `tests/game_production/test_jobs.py`

---

### Task 1: กำหนด GameIR และ parser ที่ปฏิเสธ input ผิดรูป

**Files:**
- Create: `src/book_production_platform/game_ir/__init__.py`
- Create: `src/book_production_platform/game_ir/errors.py`
- Create: `src/book_production_platform/game_ir/models.py`
- Create: `src/book_production_platform/game_ir/game-ir.v1.schema.json`
- Test: `tests/game_ir/test_models.py`

**Interfaces:**
- Consumes: UTF-8 JSON bytes จาก CLI/job layer
- Produces: `parse_game_ir(payload: bytes) -> GameIR`, `GameInputError.code == "invalid_game_ir"`
- Types: `Cell = tuple[int, int]`; `WorldIR`, `PlayerIR`, `WallIR`, `ObjectiveIR`, `ExitIR`, `GameIR` เป็น frozen strict models
- Constant: `MAX_GAME_IR_BYTES = 256 * 1024`; payload ที่ใหญ่กว่านี้ใช้ `resource_cap_exceeded`
- GameIR ใช้ field: `schema_version`, `scene_id`, `template`, `palette`, `world`, `player`, `walls`, `objectives`, `exit`; พิกัดทุกจุดเป็นคู่ integer `[x, y]`

- [ ] **Step 1: เขียน test สำหรับ JSON contract**

สร้าง test สำหรับ valid ตัวอย่างหนึ่งฉาก, byte size ที่ 256 KiB พอดี, size เกิน cap, invalid UTF-8, malformed JSON, NaN/Infinity, unknown field, wrong schema version และ missing required field

```python
def test_parse_rejects_unknown_fields_and_non_finite_numbers() -> None:
    with pytest.raises(GameInputError) as unknown:
        parse_game_ir(b'{"schema_version":"game-ir.v1","unexpected":true}')
    assert unknown.value.code == "invalid_game_ir"

    with pytest.raises(GameInputError) as non_finite:
        parse_game_ir(b'{"schema_version":"game-ir.v1","value":NaN}')
    assert non_finite.value.code == "invalid_game_ir"
```

- [ ] **Step 2: รัน test ให้ fail เพราะ package ยังไม่มี**

Run: `py -3.12 -m pytest tests/game_ir/test_models.py -q`
Expected: FAIL ที่ import `book_production_platform.game_ir`

- [ ] **Step 3: เพิ่ม strict models และ JSON parser**

กำหนด frozen Pydantic models ที่ใช้ `ConfigDict(extra="forbid", frozen=True, strict=True)`; จำกัด schema/template/palette ด้วย Literal ให้เป็น `game-ir.v1`, `top-down-collect.v1`, `palette-minimal.v1`. จำกัด `scene_id` และ entity IDs ด้วย `^[a-z0-9]+(?:-[a-z0-9]+)*$` และความยาวสูงสุด 64 ตัวอักษร. `WorldIR.width/height` อยู่ระหว่าง 3–64, `tile_size` อยู่ระหว่าง 0.25–4.0, player speed อยู่ระหว่าง 0.5–12.0, objective มี 1–32 รายการ และ payload ถูกตัดสินขนาดก่อน decode. ส่ง `json.loads(..., parse_constant=reject_constant)` เพื่อปฏิเสธ NaN/Infinity แล้วแปลง ValueError/UnicodeDecodeError/ValidationError เป็น `GameInputError("invalid_game_ir")`. กำหนด `reject_constant(value: str) -> NoReturn` ให้ raise ValueError ก่อน Pydantic.

โครงสร้างเริ่มต้นของ model:

```python
class GameIR(StrictModel):
    schema_version: Literal["game-ir.v1"]
    scene_id: str
    template: Literal["top-down-collect.v1"]
    palette: Literal["palette-minimal.v1"]
    world: WorldIR
    player: PlayerIR
    walls: tuple[WallIR, ...]
    objectives: tuple[ObjectiveIR, ...]
    exit: ExitIR


def parse_game_ir(payload: bytes) -> GameIR:
    if len(payload) > MAX_GAME_IR_BYTES:
        raise GameInputError("resource_cap_exceeded")
    value = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    return GameIR.model_validate(value)
```

เพิ่ม JSON Schema ที่สอดคล้องกับ model และ test ว่า schema version, required fields, enum IDs, dimensions และ input byte cap ตรงกับ parser

- [ ] **Step 4: รัน contract tests และ lint เฉพาะ package**

Run: `py -3.12 -m pytest tests/game_ir/test_models.py -q`
Expected: PASS ทุกกรณี

Run: `py -3.12 -m ruff check src/book_production_platform/game_ir tests/game_ir`
Expected: PASS

- [ ] **Step 5: commit เฉพาะ Task 1**

```powershell
git add src/book_production_platform/game_ir tests/game_ir
git commit -m "feat: add strict GameIR v1 contract"
```

### Task 2: ตรวจ geometry และ canonicalize IR สำหรับ fingerprint

**Files:**
- Create: `src/book_production_platform/game_ir/validation.py`
- Test: `tests/game_ir/test_validation.py`
- Modify: `src/book_production_platform/game_ir/__init__.py`

**Interfaces:**
- Consumes: `GameIR` จาก Task 1
- Produces: `validate_game_ir(ir: GameIR) -> GameIR`, `canonical_game_ir_bytes(ir: GameIR) -> bytes`, `game_ir_sha256(ir: GameIR) -> str`, `to_unity_worker_json(ir: GameIR) -> bytes`
- Helpers: `collect_entity_ids(ir: GameIR) -> tuple[str, ...]`, `reachable_cells(start: Cell, world: WorldIR, walls: frozenset[Cell]) -> frozenset[Cell]`, `canonicalize_game_ir(ir: GameIR) -> GameIR`, `worker_dto_with_cell_objects(ir: GameIR) -> dict[str, object]`

- [ ] **Step 1: เขียน test geometry, caps, connectivity และ ordering**

สร้าง test สำหรับ duplicate IDs ทุกชนิด, จุด spawn/wall/objective/exit ชนกัน, cell เกิน world bounds, wall cell ซ้ำ, wall cell รวมเกิน 192, entity รวมเกิน 256, grid ที่ objective หรือ exit เดินไปไม่ถึง, และ IR สองชุดที่เรียง arrays ต่างกันแต่มีเนื้อหาเท่ากันต้องได้ canonical bytes/hash เดียวกัน. ยืนยันว่า `to_unity_worker_json` แปลง cell array เป็น object fields `x/y` ตาม DTO. นับ entities เป็น wall cells + objectives + player + exit.

```python
def test_rejects_unreachable_exit_before_unity_launch(valid_ir: GameIR) -> None:
    blocked = replace_ir(valid_ir, walls=complete_barrier(valid_ir.world))
    with pytest.raises(GameInputError) as error:
        validate_game_ir(blocked)
    assert error.value.code == "invalid_game_geometry"
```

- [ ] **Step 2: รัน test ให้ fail**

Run: `py -3.12 -m pytest tests/game_ir/test_validation.py -q`
Expected: FAIL เพราะยังไม่มี semantic geometry validator และ canonicalizer

- [ ] **Step 3: เพิ่ม geometry validator และ deterministic encoding**

ตรวจ coordinate ทุกตัวกับ `0 <= x < width` และ `0 <= y < height`; รวม ID ของ player, wall, objective และ exit เพื่อตรวจ uniqueness. ปฏิเสธ occupancy ซ้ำระหว่าง spawn, wall, objective และ exit. ใช้ BFS 4 ทิศจาก spawn บน grid ที่ตัด wall cells ออก และปฏิเสธถ้า objective หรือ exit ใดไปไม่ถึง. ตรวจจำนวน wall cells และ entity รวมตามเพดาน. จากนั้น sort walls/objectives ตาม ID, cells ตาม `(y, x)`, serialize JSON ด้วย UTF-8, `sort_keys=True`, separators `(",", ":")`, และ hash ด้วย SHA-256. `to_unity_worker_json` สร้างแยกต่างหากเป็น serializable DTO projection โดยแปลงพิกัด `[x, y]` เป็น `{"x": x, "y": y}`; Unity worker ใช้ `JsonUtility` กับ object DTO และไม่ parse nested primitive arrays.

```python
def validate_game_ir(ir: GameIR) -> GameIR:
    ids = collect_entity_ids(ir)
    if len(ids) != len(set(ids)):
        raise GameInputError("invalid_game_geometry")
    wall_cells = {cell for wall in ir.walls for cell in wall.cells}
    occupied = {ir.player.spawn, ir.exit.cell, *(item.cell for item in ir.objectives)}
    if wall_cells & occupied or len(occupied) != len(ir.objectives) + 2:
        raise GameInputError("invalid_game_geometry")
    if len(wall_cells) != sum(len(w.cells) for w in ir.walls):
        raise GameInputError("invalid_game_geometry")
    reachable = reachable_cells(ir.player.spawn, ir.world, frozenset(wall_cells))
    if not ({item.cell for item in ir.objectives} | {ir.exit.cell}) <= reachable:
        raise GameInputError("invalid_game_geometry")
    if len(wall_cells) > 192 or len(wall_cells) + len(ir.objectives) + 2 > 256:
        raise GameInputError("resource_cap_exceeded")
    normalized = canonicalize_game_ir(ir)
    return normalized


def to_unity_worker_json(ir: GameIR) -> bytes:
    validated = validate_game_ir(ir)
    projection = worker_dto_with_cell_objects(validated)
    return json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

- [ ] **Step 4: ตรวจ regression และ property cases**

Run: `py -3.12 -m pytest tests/game_ir -q`
Expected: PASS รวม schema + geometry; IDs, topology และ canonical hash tests ผ่าน

- [ ] **Step 5: commit เฉพาะ Task 2**

```powershell
git add src/book_production_platform/game_ir tests/game_ir
git commit -m "feat: validate and canonicalize GameIR geometry"
```

### Task 3: สร้าง Unity template, scene compiler และ gameplay tests

**Files:**
- Create: `src/book_production_platform/game_production/templates/unity_project/.gitattributes` to keep serialized Unity text assets on LF across checkouts
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Runtime/GameProgress.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Runtime/TopDownPlayerController.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Runtime/Collectible.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Runtime/ExitGate.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Editor/SceneCompiler.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Editor/SceneVerifier.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Source/game-ir.json` and fixed `.meta`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Source/scene-composer-template.unity` and fixed `.meta`
- Create: `src/book_production_platform/game_production/templates/unity_project/.book-production/worker-input.json`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Art/{player,wall,objective,exit}.png` และไฟล์ `.meta` ที่มี GUID คงที่
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/EditMode/BookProduction.EditModeTests.asmdef`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/EditMode/SceneVerifierTests.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/PlayMode/BookProduction.PlayModeTests.asmdef`
- Create: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/PlayMode/GameplayTraceTests.cs`
- Create: `src/book_production_platform/game_production/templates/unity_project/Packages/manifest.json`
- Create: `src/book_production_platform/game_production/templates/unity_project/Packages/packages-lock.json`
- Create: `src/book_production_platform/game_production/templates/unity_project/ProjectSettings/ProjectVersion.txt`
- Test: Unity EditMode และ PlayMode tests ใน template

**Interfaces:**
- Consumes: Unity worker DTO projection ที่ Python วางไว้ที่ `.book-production/worker-input.json`; canonical source GameIR คงไว้ที่ `Assets/BookProduction/Source/game-ir.json`
- Produces: `Assets/Scenes/<scene_id>.unity`, `.book-production/worker-report.json`, `.book-production/gameplay-trace.json`
- Runtime events ใช้ stable codes `player_moved`, `objective_collected`, `exit_blocked`, `game_won`; trace ไม่มี timestamp, machine path หรือ random GUID
- Test helpers ใน `GameplayTraceTests.cs`: `SceneTestDriver.LoadGeneratedScene() -> GameProgress`, `SceneTestDriver.PlayCollectionTrace(GameProgress) -> IEnumerator`, `CollectionTraceWriter.Write(IReadOnlyList<string>) -> void`

- [x] **Step 1: เขียน EditMode/PlayMode tests ก่อน runtime**

สร้าง minimal Unity project shell, test assemblies และ source ของ tests ก่อน runtime code; EditMode tests เปิด scene ที่ compiler สร้างแล้วตรวจ player หนึ่งตัว, wall colliders, objective count, exit, unique entity IDs และไม่มี external resource. PlayMode tests ใช้ test input source ป้อน fixed-step trace และยืนยัน movement, ชน wall ไม่ทะลุ, เก็บ objective เพิ่ม count, exit ก่อนเวลาไม่ชนะ และชนะหลังเก็บครบเท่านั้น.

```csharp
[UnityTest]
public IEnumerator ExitWinsOnlyAfterEveryObjectiveIsCollected()
{
    var progress = SceneTestDriver.LoadGeneratedScene();
    yield return SceneTestDriver.PlayCollectionTrace(progress);
    Assert.That(progress.CollectedCount, Is.EqualTo(progress.TotalObjectives));
    Assert.That(progress.IsWon, Is.True);
    CollectionTraceWriter.Write(progress.Events);
}
```

- [x] **Step 2: ตรวจว่าคำสั่ง Unity test แสดง test ที่ fail**

Run:

```powershell
$unity = if ($env:UNITY_BIN) { $env:UNITY_BIN } else { "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe" }
$project = "src\book_production_platform\game_production\templates\unity_project"
New-Item -ItemType Directory -Force -Path ".runtime\game-production" | Out-Null
$results = ".runtime\game-production"
& $unity -runTests -batchmode -projectPath $project -testPlatform EditMode -testResults "$results\editmode.xml" -logFile "$results\editmode.log"
```

Expected: FAIL ระหว่าง C# compile เพราะ runtime/scene worker types ยังไม่มี (CS0246); test assembly ถูกค้นพบและ log อยู่ใต้ `.runtime` เท่านั้น

- [x] **Step 3: เพิ่ม runtime scripts และ compiler**

ใช้ `Rigidbody2D` + `BoxCollider2D` สำหรับ player/wall และ trigger collider สำหรับ collectible/exit. `GameProgress` เก็บ `HashSet<string> collectedIds`, `int CollectedCount`, `int TotalObjectives`, `bool IsWon` และ event list; `TryCollect(id)` เพิ่ม count ได้ครั้งเดียวต่อ ID. `ExitGate` บันทึก `exit_blocked` จน count เท่ากับ objective total แล้วจึงตั้ง `IsWon` และ emit `game_won`. `KeyboardGameInputSource` map WASD/arrow keys เป็น `move_x`/`move_y`; controller จำกัด magnitude ที่ 1 และรับ test input ผ่าน `IGameInputSource` ที่ test assembly เปลี่ยนแทนได้. Compiler อ่าน DTO จากไฟล์ fixed path เท่านั้น, ใช้ scene composer template ที่ commit ไว้ซึ่งมี player/exit/boundary และ wall/objective slots ที่มี Unity local fileID คงที่ แล้วอัปเดตหรือถอด slot ตาม GameIR; ห้ามสร้าง GameObject ใหม่ต่อ job เพราะ Unity กำหนด local fileID ใหม่ในแต่ละครั้ง. เพิ่ม world boundary colliders และใช้ PNG palette assets ที่อยู่ใน template. Scene meta และ PNG meta ใช้ GUID คงที่; ห้ามประกอบ Unity YAML ด้วย string.

เพิ่ม `ProjectVersion.txt` เป็น `m_EditorVersion: 6000.6.2f1`; manifest/lock file ระบุเฉพาะ Test Framework 1.8.0 และ dependency ที่มีใน Unity BuiltInPackages. Compiler อ่าน object DTO ด้วย `JsonUtility.FromJson<UnityWorkerIR>` จาก path คงที่นอก Assets; ห้ามวาง input values ลง C# หรือ Unity YAML. Template ไม่มี network client/component, external URL หรือ package นอก allowlist; EditMode verifier ตรวจ component allowlist ของ scene และ source scan ปฏิเสธ `UnityWebRequest`, network manager หรือ external URL ใน trusted template. Worker เขียน version, scene counts, test trace และ stable status code ลง JSON ใน `.book-production/`; job ลบไฟล์ชั่วคราวเหล่านี้ก่อน publish.

```csharp
[Serializable]
public sealed class UnityWorkerIR
{
    public string schema_version;
    public string scene_id;
    public string template;
    public string palette;
    public WorldDTO world;
    public PlayerDTO player;
    public WallDTO[] walls;
    public ObjectiveDTO[] objectives;
    public ExitDTO exit;
}

[Serializable] public sealed class CellDTO { public int x; public int y; }
[Serializable] public sealed class WorldDTO { public int width; public int height; public float tile_size; }
[Serializable] public sealed class PlayerDTO { public string id; public CellDTO spawn; public float speed; }
[Serializable] public sealed class WallDTO { public string id; public CellDTO[] cells; }
[Serializable] public sealed class ObjectiveDTO { public string id; public CellDTO cell; }
[Serializable] public sealed class ExitDTO { public string id; public CellDTO cell; }
```

ตัวอย่าง `.book-production/worker-input.json` สำหรับ smoke scene:

```json
{
  "schema_version": "game-ir.v1",
  "scene_id": "smoke-room",
  "template": "top-down-collect.v1",
  "palette": "palette-minimal.v1",
  "world": {"width": 8, "height": 6, "tile_size": 1.0},
  "player": {"id": "player", "spawn": {"x": 1, "y": 1}, "speed": 5.0},
  "walls": [{"id": "wall-a", "cells": [{"x": 3, "y": 1}]}],
  "objectives": [{"id": "objective-a", "cell": {"x": 4, "y": 1}}],
  "exit": {"id": "exit", "cell": {"x": 6, "y": 1}}
}
```

```csharp
public bool TryCollect(string id)
{
    if (!collectedIds.Add(id)) return false;
    CollectedCount++;
    Events.Add("objective_collected:" + id);
    return true;
}

public void TryWin(string exitId)
{
    if (CollectedCount != TotalObjectives) Events.Add("exit_blocked:" + exitId);
    else { IsWon = true; Events.Add("game_won:" + exitId); }
}
```

- [x] **Step 4: สร้าง smoke scene แล้วรัน EditMode/PlayMode tests**

Run:

Start with the Unity batch compile, then run both test platforms from the same PowerShell session:

```powershell
$unity = if ($env:UNITY_BIN) { $env:UNITY_BIN } else { "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe" }
$project = "src\book_production_platform\game_production\templates\unity_project"
New-Item -ItemType Directory -Force -Path ".runtime\game-production" | Out-Null
$results = ".runtime\game-production"
```

Run:

```powershell
& $unity -batchmode -quit -projectPath $project -executeMethod BookProduction.Editor.SceneCompiler.BuildFromGameIR -logFile "$results\compile.log"
if ($LASTEXITCODE -ne 0) { throw "Unity scene compile failed; inspect the capped log." }
& $unity -runTests -batchmode -projectPath $project -testPlatform EditMode -testResults "$results\editmode.xml" -logFile "$results\editmode.log"
```

Expected: PASS; XML มี tests > 0, failures = 0, not-run = 0

Run:

```powershell
& $unity -runTests -batchmode -projectPath $project -testPlatform PlayMode -testResults "$results\playmode.xml" -logFile "$results\playmode.log"
```

Expected: PASS พร้อม trace ที่ยืนยัน blocked exit ก่อนเก็บครบและ win หลังเก็บครบ

- [x] **Step 5: commit เฉพาะ Task 3**

```powershell
git add src/book_production_platform/game_production/templates/unity_project
git commit -m "feat: add deterministic Unity collection scene template"
```

### Task 4: ห่อ Unity batch process ด้วย version, timeout และ log gates

**Files:**
- Create: `src/book_production_platform/game_production/errors.py`
- Create: `src/book_production_platform/game_production/runtime.py`
- Test: `tests/game_production/test_runtime.py`

**Interfaces:**
- Consumes: Unity executable path, project path, stage name, monotonic job deadline
- Produces: `UnityProcessResult(version: str, executable_sha256: str, returncode: int, log_bytes: int, elapsed_seconds: float, test_xml: Path | None)`
- Runner API: `run_unity_stage(unity_bin: Path, project_path: Path, stage: str, deadline: float) -> UnityProcessResult`; tests capture exact argv, cwd and shell flag
- Test capture: `capture_unity_invocation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CapturedInvocation`; captured fields are `argv: list[str]`, `cwd: Path`, `shell: bool`
- Worker entrypoints: `BookProduction.Editor.SceneCompiler.BuildFromGameIR` และ `BookProduction.Editor.SceneVerifier.VerifyGeneratedScene`

- [x] **Step 1: เขียน process runner tests**

ใช้ monkeypatch fake runner ทดสอบ argv เป็น list, `shell=False`, Unity version mismatch, executable หาย, timeout, log เกิน 8 MiB, non-zero exit, test XML หาย และ XML ที่มี failed/not-run tests. ยืนยันว่า command ไม่มี GameIR JSON หรือ field text.

```python
def test_unity_argv_never_contains_game_ir_payload(monkeypatch, tmp_path):
    captured = capture_unity_invocation(monkeypatch, tmp_path)
    run_unity_stage(UNITY_BIN, tmp_path, "compile", deadline_after(120))
    assert isinstance(captured.argv, list)
    assert "--scene-name-from-user" not in " ".join(captured.argv)
    assert captured.shell is False
```

- [x] **Step 2: รัน test ให้ fail**

Run: `py -3.12 -m pytest tests/game_production/test_runtime.py -q`
Expected: FAIL เพราะยังไม่มี error model หรือ Unity runner

- [x] **Step 3: implement subprocess supervisor**

สร้าง argv คงรูปแบบ `[unity, "-batchmode", "-quit", "-projectPath", project, ...stage_args, "-logFile", log]`; ใช้ `subprocess.Popen(..., shell=False)`, poll process ด้วย monotonic clock และ terminate/kill เมื่อเวลารวมหมดหรือ log โตเกิน 8 MiB. ทุก stage ใช้ deadline เดียวกันที่ไม่เกิน 120 วินาที. ตรวจ return code, worker report version, Unity executable SHA-256 และ test XML ที่ต้องมีอย่างน้อยหนึ่ง test และไม่มี failure/skip. Stable codes: `unity_unavailable`, `unsupported_unity_version`, `deadline_exceeded`, `unity_log_cap_exceeded`, `unity_worker_failed`, `unity_test_failed`.

```python
process = subprocess.Popen(
    argv, cwd=project_path, shell=False,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
while process.poll() is None:
    if time.monotonic() >= deadline:
        process.kill()
        process.wait()
        raise GameJobError("deadline_exceeded")
    log_bytes = log_path.stat().st_size if log_path.exists() else 0
    if log_bytes > MAX_UNITY_LOG_BYTES:
        process.kill()
        process.wait()
        raise GameJobError("unity_log_cap_exceeded")
    time.sleep(0.1)
if process.returncode != 0:
    raise GameJobError("unity_worker_failed")
```

- [x] **Step 4: รัน process runner regression tests**

Run: `py -3.12 -m pytest tests/game_production/test_runtime.py -q`
Expected: PASS ทุก exit/timeout/log/XML branch

- [x] **Step 5: commit เฉพาะ Task 4**

```powershell
git add src/book_production_platform/game_production/errors.py src/book_production_platform/game_production/runtime.py tests/game_production/test_runtime.py
git commit -m "feat: supervise Unity batch jobs safely"
```

### Task 5: ประกอบ job, receipt, replay และ atomic publication

**Files:**
- Create: `src/book_production_platform/game_production/__init__.py`
- Create: `src/book_production_platform/game_production/jobs.py`
- Create: `src/book_production_platform/game_production/qa.py`
- Modify: `src/book_production_platform/game_production/templates/unity_project/Assets/BookProduction/Editor/SceneVerifier.cs`
- Modify: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/EditMode/SceneVerifierTests.cs`
- Modify: `src/book_production_platform/game_production/templates/unity_project/Assets/Tests/PlayMode/GameplayTraceTests.cs`
- Modify: `pyproject.toml` (include Unity template files in package builds)
- Test: `tests/game_production/test_jobs.py`

**Interfaces:**
- Consumes: `GameIR`, Unity executable, output root
- Produces: `compose_game_scene(game_path: Path, output_root: Path, unity_bin: Path, timeout_s: int = 120) -> GameJobResult`
- `GameJobResult`: status, fingerprint, artifact_dir, artifact_hashes, replay, model_calls=0
- Output: `<output-root>/<fingerprint>/project/`, `qa-manifest.json`, `.receipt.json`; receipt อยู่ใน staging artifact เดียวกันเพื่อให้ directory rename publish ทั้งชุดพร้อมกัน
- Helpers: `fingerprint_lock(root: Path, fingerprint: str, deadline: float) -> Iterator[None]`, `receipt_exists(artifact_dir: Path) -> bool`, `verify_replay_and_load_result(artifact_dir: Path) -> GameJobResult`, `create_job_staging(root: Path, fingerprint: str) -> Path`, `run_compile_tests_and_reopen(project: Path, game_ir: GameIR, unity_bin: Path, deadline: float) -> UnityQaEvidence`, `write_manifest_and_receipt(staging: Path, game_ir: GameIR, fingerprint: str, evidence: UnityQaEvidence) -> GameJobResult`, `atomic_publish(staging: Path, destination: Path) -> None`, `remove_only_job_staging(staging: Path) -> None`

- [x] **Step 1: เขียน tests ให้ job lifecycle**

ทดสอบ first build ผ่าน fake Unity report, exact replay ไม่เรียก process runner ซ้ำ, source change สร้าง fingerprint ใหม่, artifact/manifest/receipt corruption ถูกปฏิเสธ, symlink path ถูกปฏิเสธ, staging > 1 GiB หรือ published project > 64 MiB ไม่มี accepted output, process failure/timeout ไม่มี accepted output, staging cleanup จำกัดเฉพาะ job ปัจจุบัน และ output destination ที่มีอยู่ไม่ถูกเขียนทับ.

```python
def test_replay_returns_receipt_without_rerunning_unity(game_job, fake_unity):
    first = compose_game_scene(**game_job)
    second = compose_game_scene(**game_job)
    assert first.artifact_hashes == second.artifact_hashes
    assert second.replay is True
    assert fake_unity.call_count == 4
```

- [x] **Step 2: รัน test ให้ fail**

Run: `py -3.12 -m pytest tests/game_production/test_jobs.py -q`
Expected: FAIL เพราะ job, receipt และ publication APIs ยังไม่มี

- [x] **Step 3: implement content-addressed transaction**

อ่าน input ด้วยขนาด cap, parse + semantic validate, คำนวณ IR SHA-256 และ fingerprint จาก compiler/schema version, Unity version, Unity binary SHA-256 และ hash ของ Unity template ทั้งชุด. ใช้ per-fingerprint cross-process lock; ตรวจ receipt ก่อนเริ่ม Unity. สร้าง project ใน `<output-root>/.staging/g<fingerprint-prefix>-<random>/project` โดยใช้ชื่อ staging สั้นเพื่อเลี่ยง Windows MAX_PATH, copy เฉพาะ template files ที่กำหนด, เขียน canonical source JSON ลง fixed asset path และ worker DTO ลง `.book-production/worker-input.json`, รัน compiler → EditMode → PlayMode → fresh-process scene verifier ภายใต้ deadline เดียว, ตรวจ gameplay event trace และ scene hierarchy, ลบ `.book-production`, Temp/Library/Logs/UserSettings จาก deliverable แล้ว hash เฉพาะ Assets/Packages/ProjectSettings.

เขียน `game-production.qa.v1` ที่มี source/normalized IR hashes, worker/schema/template/Unity versions, checks, gameplay event counts, output relative paths/hashes/bytes, staging bytes, elapsed seconds, replay=false, `model_calls: 0`, `monetary_cost: "unknown"`. ห้าม manifest มี absolute path. ปฏิเสธถ้า staging tree เกิน 1 GiB หรือ project output เกิน 64 MiB. เขียน `.receipt.json` ที่ pin project tree hash, scene hash และ manifest hash ไว้ใน staging directory. Flush ไฟล์ทั้งหมด แล้ว publish staging directory ด้วย atomic rename เฉพาะเมื่อปลายทางยังไม่มีอยู่; replay ตรวจทุก hash และคืน artifact เดิมโดยไม่ launch Unity.

```python
with fingerprint_lock(output_root, fingerprint, deadline):
    destination = output_root / fingerprint
    if receipt_exists(destination):
        return verify_replay_and_load_result(destination)
    staging = create_job_staging(output_root, fingerprint)
    try:
        evidence = run_compile_tests_and_reopen(staging / "project", game_ir, unity_bin, deadline)
        result = write_manifest_and_receipt(staging, game_ir, fingerprint, evidence)
        atomic_publish(staging, destination)
        return result
    except Exception:
        remove_only_job_staging(staging)
        raise
```

- [x] **Step 4: รัน job integrity tests**

Run: `py -3.12 -m pytest tests/game_production/test_jobs.py -q`
Expected: PASS; corruption fail-closed, replay ไม่ rerun, publish ไม่เขียนทับ

- [x] **Step 5: commit เฉพาะ Task 5**

```powershell
git add src/book_production_platform/game_production tests/game_production/test_jobs.py
git commit -m "feat: publish verified GameIR Unity projects"
```

### Task 6: เพิ่ม CLI, ตัวอย่าง GameIR และ usage docs

**Files:**
- Modify: `src/book_production_platform/cli.py`
- Create: `src/book_production_platform/game_production/cli.py`
- Create: `examples/game/collection-room.json`
- Create: `tests/game_production/test_cli.py`
- Modify: `tests/test_platform_cli.py`
- Modify: `scripts/check.py`
- Modify: `README.md`

**Interfaces:**
- `book-production game build --game JSON_PATH --output-root OUTPUT_ROOT --unity UNITY_EXECUTABLE [--timeout-s 120]`
- `book-production game benchmark --fixtures FIXTURES_DIR --output REPORT_PATH --unity UNITY_EXECUTABLE`
- Python entrypoint: `game_main(argv: list[str]) -> int`
- CLI success ส่ง JSON ที่มี status, fingerprint, artifact path, hashes, replay และ model_calls; rejection ส่ง stable error code และ exit code 2

- [x] **Step 1: เขียน CLI tests**

ทดสอบ parser สำหรับ subcommands/required args, success JSON, rejection JSON, exit code, timeout cap และกรณี path ของ Unity ไม่มีอยู่.

```python
def test_game_build_reports_model_calls_zero(monkeypatch, capsys):
    monkeypatch.setattr(game_cli, "compose_game_scene", fake_game_job)
    assert game_cli.main(["build", "--game", "room.json", "--output-root", "jobs", "--unity", "Unity.exe"]) == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
```

- [x] **Step 2: รัน CLI tests ให้ fail**

Run: `py -3.12 -m pytest tests/game_production/test_cli.py tests/test_platform_cli.py -q`
Expected: FAIL เพราะ `game` command ยังไม่อยู่ใน parser

- [x] **Step 3: เพิ่ม CLI, example และคู่มือ**

เพิ่ม `game` dispatch โดยไม่แก้ behavior ของ `scene`, `graphics` หรือ `spreadsheet`. ตัวอย่างมี world 12 × 8, spawn หนึ่งจุด, กำแพงสอง cell, objective หนึ่งจุด และ exit ที่ BFS เดินถึง. README ระบุ dependency/runtime pin, ตัวอย่างเรียก build/benchmark, output files, stable error codes, model_calls=0, cap และ local pilot boundary.

```python
game = groups.add_parser("game", help="Unity Game Scene Builder operations")
game.add_argument("game_args", nargs=argparse.REMAINDER)
if args.family == "game":
    return game_main(args.game_args)
```

- [x] **Step 4: รัน CLI tests และ static checks**

Run: `py -3.12 -m pytest tests/game_production/test_cli.py tests/test_platform_cli.py -q`
Expected: PASS

Run: `py -3.12 -m ruff check src/book_production_platform/game_ir src/book_production_platform/game_production tests/game_ir tests/game_production`
Expected: PASS

- [x] **Step 5: commit เฉพาะ Task 6**

```powershell
git add src/book_production_platform/cli.py src/book_production_platform/game_production/cli.py examples/game tests/game_production/test_cli.py tests/test_platform_cli.py scripts/check.py README.md
git commit -m "feat: expose Game Scene Builder CLI"
```

### Task 7: ทำ benchmark 20 cases และเก็บ acceptance evidence

**Files:**
- Create: `src/book_production_platform/game_production/benchmark.py`
- Create: `src/book_production_platform/game_production/scripts/benchmark.py`
- Create: `tests/fixtures/game_production/accepted/*.json` (5)
- Create: `tests/fixtures/game_production/rejected/*.json` (9)
- Create: `tests/fixtures/game_production/README.md`
- Create: `tests/game_production/test_benchmark.py`
- Modify: `README.md`

**Interfaces:**
- `run_benchmark(fixtures_dir: Path, output_path: Path, unity_bin: Path) -> dict[str, object]`
- Benchmark helpers: `run_game_fixture(path: Path) -> str`, `run_rejection_fixture(path: Path) -> str`, `run_tampered_replay_case(path: Path) -> str`, `summarize(outcomes: list[str]) -> dict[str, int]`
- Report มี accepted/rejected/replay/tamper counts, stable error codes, fresh/replay latency, project bytes, test trace summary และ `model_calls: 0`
- Benchmark ประกอบด้วย accepted 5 งาน, replay ซ้ำ 5 งาน และ rejection/tamper 10 งาน

- [x] **Step 1: เขียน benchmark tests พร้อม fake job runner**

กำหนด 5 accepted scene fixtures: minimal room, multi-wall route, two objectives, near-wall movement, และ larger valid room. กำหนด 9 rejection fixtures: malformed JSON, oversized JSON, unknown/code-like field, unsupported template, duplicate ID, out-of-bounds, overlap, non-finite/out-of-range value และ count cap. กรณีที่ 10 สร้าง accepted artifact แล้วแก้ scene hash ก่อน replay เพื่อยืนยัน tamper rejection.

```python
def test_benchmark_requires_exact_case_counts(report):
    assert report["accepted"] == 5
    assert report["replays"] == 5
    assert report["rejected"] == 10
    assert report["unexpected_outcomes"] == 0
    assert report["model_calls"] == 0
```

- [x] **Step 2: รัน benchmark tests ให้ fail**

Run: `py -3.12 -m pytest tests/game_production/test_benchmark.py -q`
Expected: FAIL เพราะ benchmark runner ยังไม่มี

- [x] **Step 3: implement benchmark และ report**

รัน 5 accepted jobs จริงและบันทึกผล; เรียกซ้ำแต่ละ job อีกครั้งเพื่อบังคับ replay hit โดยไม่เปิด Unity; รัน 9 invalid inputs ที่ต้อง reject ก่อน Unity; ใช้ accepted artifact หนึ่งชุดสำหรับ tamper test. Benchmark failure เมื่อ counts ไม่ตรง, worker ถูกเรียกตอน replay, gameplay trace ผิด, output hash เปลี่ยน, stable code ไม่ตรง หรือ `model_calls != 0`. ห้ามเก็บ absolute path หรือ raw input ในรายงาน.

```python
for fixture in accepted_fixtures:
    first = run_game(fixture)
    outcomes.append(first.status)
    outcomes.append(run_game(fixture).status)
for fixture in rejected_fixtures:
    outcomes.append(run_expected_rejection(fixture))
outcomes.append(run_tampered_replay_case(accepted_fixtures[0]))
assert summarize(outcomes) == {"accepted": 5, "replays": 5, "rejected": 10}
```

- [x] **Step 4a: รัน automated acceptance บน Unity ที่ pin**

Run:

```powershell
$unity = if ($env:UNITY_BIN) { $env:UNITY_BIN } else { "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe" }
book-production game build --game examples/game/collection-room.json --output-root .runtime/game-production --unity "$unity"
book-production game benchmark --fixtures tests/fixtures/game_production --output .runtime/game-production/benchmark.json --unity "$unity"
```
Expected: 5 accepted, 5 exact replays, 10 stable rejections, 0 unexpected outcomes, 0 model calls; ทุก accepted scene ผ่าน EditMode/PlayMode และ reopen verification ภายใน 120 วินาทีต่องาน

หลักฐานรอบล่าสุดอยู่ที่ `.runtime/game-production/benchmark-final.json`: 5 builds, 5 exact replays, 9 invalid-input rejections, 1 tampered-replay rejection, 0 unexpected outcomes, EditMode 10/10, PlayMode 5/5 และ `model_calls: 0`. Unity version/hash และ latency/bytes บันทึกไว้ใน README.

- [ ] **Step 4b: เปิด scene ใน Unity Editor และตรวจด้วยมือ**

เปิด scene จาก accepted artifact ใน Unity Editor 6000.6.2f1, ตรวจ hierarchy ว่ามี Player/Walls/Objectives/Exit และเล่นหนึ่งรอบ; บันทึก editor version, scene fingerprint และผลตรวจด้วยมือไว้ใน benchmark evidence โดยไม่เก็บ private payload. สถานะปัจจุบัน `manual_scene_review: pending`; ต้องให้ผู้ใช้ตัดสินใจเรื่อง Unity Editor Software Terms ก่อนเปิดตรวจด้วยมือ.

- [x] **Step 5: รันชุดตรวจ package ทั้งหมด**

Run: `py -3.12 -m pytest -q`
Expected: PASS ทุก test ใน book-production-platform

Run: `py -3.12 -m ruff check src/book_production_platform/game_ir src/book_production_platform/game_production tests/game_ir tests/game_production`
Expected: PASS

Run: `py -3.12 -m mypy --strict src/book_production_platform/game_ir src/book_production_platform/game_production`
Expected: PASS

- [x] **Step 6: commit เฉพาะ Task 7**

Commit: `509d3d1 test: add Game Scene Builder acceptance benchmark`.

```powershell
git add src/book_production_platform/game_production/benchmark.py src/book_production_platform/game_production/scripts tests/fixtures/game_production tests/game_production/test_benchmark.py README.md
git commit -m "test: add Game Scene Builder acceptance benchmark"
```

### Task 8: ตรวจทานขอบเขต, Git diff และ pilot status

**Files:**
- Modify: `README.md`
- Modify: `tests/fixtures/game_production/README.md`

- [x] **Step 1: ตรวจ acceptance evidence และ diff**

Run: `git status --short`
Expected: แสดงเฉพาะงาน Game V1A ใน checkout `book-production-platform`; ไม่ stage ไฟล์ใน Solo Empire หรือ nested repo อื่น

Run: `git diff --check`
Expected: ไม่มี whitespace error

- [x] **Step 2: ตรวจ readiness statements**

README ระบุผล benchmark ที่รันจริง, Unity build/hash, test counts, model calls, latency/bytes และ manual scene review. ถ้า Unity license/batch mode ใช้ไม่ได้ ให้แสดง `runtime_acceptance: blocked` พร้อม stable error; ห้ามอ้างว่า Python tests แทน Unity gameplay tests ได้. ไม่สร้าง remote, release หรือ deployment target.

- [x] **Step 3: ตรวจ repo suite หลังรวม**

Run: `py -3.12 scripts/check.py`
Expected: pytest, Ruff และ strict mypy ผ่าน

- [x] **Step 4: จบ pilot โดยคงขอบเขต artifact**

Commit: `72c5fca docs: record Game Scene Builder pilot readiness`. Artifact และ benchmark JSON อยู่ใต้ `.runtime`; ไม่มี generated Unity project ถูกเพิ่มใน Git หรือ Solo Empire root.

ผลที่ review ได้คือ source code ใน `book-production-platform`, benchmark JSON ใต้ `.runtime` ซึ่ง ignore อยู่ และ Unity project หนึ่งชุดที่ output root ที่ระบุ. Generated project ไม่ถูกคัดลอกเข้า Solo Empire.

## Rollback

ปิดคำสั่ง `game` จาก CLI หรือเลือก `game-scene-composer.v1a.1`/template version ก่อนหน้า. ลบได้เฉพาะ staging directory ของ fingerprint ปัจจุบัน; ห้ามลบ output เก่าที่ publish แล้วโดย job runner. ไม่มี database migration, network side effect, store publishing หรือ deployment ในรุ่นนี้.

## Unity References

- [Unity 6.0 Test Framework package overview](https://docs.unity3d.com/cn/6000.0/Manual/com.unity.test-framework.html)
- [Unity 6.0 Editor command-line arguments](https://docs.unity3d.com/cn/6000.0/Manual/EditorCommandLineArguments.html)
- [Test Framework command-line arguments](https://docs.unity3d.com/Packages/com.unity.test-framework@2.0/manual/reference-command-line.html) (reference docs v2.0; the local pilot pins the bundled v1.8 package and verifies it with batch tests)
