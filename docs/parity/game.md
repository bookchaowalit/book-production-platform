# Game V1A — feature parity with the Unity and Godot editors

Scope: `game_production` + `game_ir` (tool id `game-v1a`). The engine is a
deterministic scene composer for one template, `top-down-collect.v1`: a strict
GameIR grid (player, wall cells, objectives, exit, optional local audio) is
compiled by a pinned Unity 6 (6000.6.2f1) Editor in batch mode into an editable
project, verified (scene hierarchy report, EditMode + PlayMode tests, gameplay
trace, fresh-process reopen), and published content-addressed. `game export`
builds a Web or Windows player from an accepted project. It is not a general
game editor.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope.

Honesty note: Unity is not installed on the host used for this change. Trace,
guide, metrics, and map are verified at the GameIR level with the existing fake
Unity stages; the Unity Editor click steps are derived from `SceneCompiler.cs`
and are not runtime-verified.

## Capability matrix

| Capability | Unity / Godot | Engine status | Priority | Notes |
|---|---|---|---|---|
| 2D top-down scene from data | yes | have | — | one GameObject per entity at `CellCenter`, scale = tile |
| Grid / tilemap | Tilemap + Grid / TileMapLayer | partial | P2 | grid is in the IR; Unity scene uses GameObjects (no Tilemap). Guide offers a Grid with Cell Size = tile as a snapping aid |
| Player controller | Input System / CharacterBody2D | have | — | WASD/arrows, speed from IR, Rigidbody2D continuous |
| Collectibles + win condition | scripting | have | — | `GameProgress`, `Collectible`, `ExitGate`, gameplay events |
| Walls / colliders / world bounds | yes | have | — | BoxCollider2D walls + 4 boundaries |
| Camera framing | Cinemachine / Camera2D | partial | P3 | fixed orthographic size `max(w, h) × tile × 0.6` |
| Audio (music + collect SFX) | yes | have | — | GameIR v2, local hashed assets |
| Automated tests (EditMode / PlayMode) | Test Runner / GUT | have | — | required for acceptance |
| Player builds (Web, Windows) | Build Profiles / Export | have | — | `game export` |
| Reachability validation | — | have | — | BFS: every objective and the exit reachable from spawn |
| Level metrics / difficulty | Analytics, custom tools | **have (new)** | — | `level-metrics.json`: shortest collect-all route (exact up to 12 objectives, heuristic above), steps, est. seconds, dead ends, junctions, detour ratio, heuristic difficulty band |
| Level map preview | Scene view / editor screenshots | **have (new)** | — | `level-map.svg` with route, `ascii_map` in the guide |
| Traceability per run | — | **have (new)** | — | `trace.json` (validate, assets, runtime/template hashes, Unity stages + test totals, gameplay QA, publish), `history.jsonl`, `ir.json`; compose and export, success and failure |
| "How would I do this by hand" | tutorials | **have (new)** | — | Unity 6 guide: every entity's name, cell, exact Transform position, components and values; Play mode check in route order; Build Profiles guide for export |
| Moving hazards / enemies | yes | missing | P1 | needs IR v3 entity + patrol validator (time-expanded reachability) + SceneCompiler + PlayMode test + V1B allowlist; not added here because the Unity side cannot be verified on this host |
| Multiple levels / scene flow | Build Settings scene list / SceneTree | missing | P1 | a level-pack IR + per-level metrics + difficulty curve; needs SceneCompiler multi-scene support |
| Doors / keys / switches | scripting | missing | P2 | |
| Animation / sprite sheets | Animator / AnimationPlayer | missing | P3 | palette sprites only |
| UI (HUD, score, menus) | uGUI / UI Toolkit / Control | missing | P2 | |
| Physics puzzles, tile properties | yes | missing | P3 | |
| Arbitrary scripting | C# / GDScript | — | — | out of scope: trusted template scripts only |
| Mobile / console targets | yes | missing | P3 | Web + Windows only |
| AI brief → GameIR (V1B) | — | have | — | allowlist unchanged: no new entity types were added in this change |

## Implemented in this change

1. Trace + history + Unity manual guide for every compose and export run with
   valid input; artifacts hashed in place for `production_trace verify`.
2. Deterministic level metrics with an optimal collect-all route
   (`game_production/metrics.py`).
3. Top-down level map SVG + ASCII map (`game_production/level_map.py`).
4. AI provenance sidecar from `game brief`, honoured only on a hash match.

## Roadmap (prioritised)

1. P1 — Moving hazards: `hazards: [{id, path: [cells], period_steps}]` in a
   GameIR v3, validator proving a safe collect-all schedule exists
   (time-expanded BFS), SceneCompiler patrol component, PlayMode test, V1B
   allowlist + eval cases.
2. P1 — Level packs: ordered levels, per-level metrics, monotone difficulty
   check, one scene per level in Build Profiles.
3. P2 — Tilemap output option (walls as a Tilemap with a CompositeCollider2D)
   to match how designers usually build grid levels.
4. P2 — HUD with collected / total counter.
5. P3 — Camera follow for worlds larger than one screen.
