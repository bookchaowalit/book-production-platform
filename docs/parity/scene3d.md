# Scene3D V1A — feature parity with Blender and KeyShot

Scope: `scene3d` (tool id `scene3d-v1a`). The engine takes one preflighted
local GLB and a `scene-ir.v1` made only of registered presets, builds an
editable studio scene in the pinned Blender 5.2.2 LTS, reopens it in a fresh
process, and renders a PNG. It is a product-hero recipe, not a general 3D
editor: SceneIR has no free numbers, so every look is a named preset.

Status: **have** / **partial** / **missing**; priority P1 next, P2 later,
P3 on demand, — out of scope.

## Capability matrix

| Capability | Blender / KeyShot | Engine status | Priority | Notes |
|---|---|---|---|---|
| Import GLB/glTF 2.0 | yes | have | — | self-contained GLB only, strict preflight (no external buffers, URLs, animation, unapproved extensions) |
| Import FBX / OBJ / USD / STEP | yes | missing | P2 | convert to GLB upstream for now |
| Auto-fit product to frame | KeyShot "fit", Blender manual | have | — | `*-fit.v1` compositions back the camera off to the bounding sphere × 1.15 |
| Camera angle presets | manual / camera presets | have | — | three-quarter, three-quarter-fit, front, side, top, plus 8 orbit angles |
| Free camera position / focal length | yes | — | — | by design SceneIR has no free numbers; lens fixed at 55 mm |
| Turntable (360° set of angles) | KeyShot turntable animation, Blender orbit rig | **partial (new)** | P1 | `scene3d.turntable` writes one SceneIR per 45° step (1/2/4/8 frames), each composed and traced as its own job; no animated video/GIF encode |
| Lighting presets | KeyShot light presets / HDRIs | **have (new: 3 presets)** | — | `softbox-three-point.v1`, `high-key-white.v1`, `dramatic-rim.v1` (area disk lights) |
| HDRI environment lighting | core in KeyShot | missing | P1 | needs a bundled, licensed HDRI asset and a size cap |
| Background presets | backdrop / environment colour | **have (new: 3)** | — | `studio-neutral.v1`, `studio-white.v1`, `transparent.v1` |
| Transparent background (cut-out PNG) | Film ▸ Transparent / KeyShot alpha | **have (new)** | — | RGBA PNG, no ground plane |
| Shadow catcher under transparent product | Blender shadow catcher / KeyShot ground shadows | missing | P1 | transparent output currently drops the floor shadow |
| Material override presets (matte, gloss, metal) | KeyShot material library | missing | P1 | only `preserve-source.v1` (GLB materials as authored) |
| Render engine | EEVEE, Cycles / KeyShot CPU+GPU | partial | P2 | EEVEE 64 samples in production; Cycles CPU is a benchmark candidate only |
| Resolution profiles | any | partial | P2 | `square-1024.v1`, `landscape-1920x1080.v1`; no 4K or 4:5 |
| Depth of field | yes | missing | P3 | |
| Render passes / AOVs, multi-object scenes, props | yes | missing | P3 | |
| Editable source file | `.blend` / `.bip` | have | — | `scene.blend` with packed textures |
| Replay / content addressing | — | have | — | fingerprint of IR + asset + Blender build; tamper rejected |
| History, provenance, redo by hand | — | **have (new)** | — | `trace.json` + `history.jsonl` + Blender `MANUAL-STEPS.md` |

## Implemented in this pass

1. **More lighting presets**: `high-key-white.v1` (four neutral softboxes) and
   `dramatic-rim.v1` (small warm key, two strong cool rims). Values live in
   `scene_builder.LIGHTING_PRESETS`, used by both the Blender builder and the
   manual guide.
2. **Backgrounds**: `studio-white.v1` and `transparent.v1`
   (`film_transparent`, RGBA PNG, no ground). `render_saved_scene` keeps RGBA
   when the saved scene is transparent.
3. **Turntable batch spec**: eight `orbit-fit-NNN.v1` compositions and
   `python -m book_production_platform.scene3d.turntable --scene … --output-dir … --steps 8`.

The V1B brief adapter does not propose these engine-only presets yet
(`scene_builder.ENGINE_ONLY_PRESETS`): its 30-case eval set does not cover
them. Structured SceneIR may use them directly.

**Runtime-dependent, not verified here:** Blender is not installed in this
environment, so the new presets are tested at SceneIR, builder-allowlist,
trace, and guide level with fake workers. Their look (exposure of the
high-key and rim rigs, alpha edges of the transparent render) still needs a
render on the pinned Blender build and a visual review before they are
offered to clients.

## Roadmap (prioritised)

1. P1 — Render the new presets on Blender 5.2.2 LTS and add golden images.
2. P1 — Shadow catcher for `transparent.v1`.
3. P1 — Material override presets (matte, gloss, brushed metal) with a fixed library.
4. P1 — HDRI environment preset with a licensed bundled HDRI.
5. P1 — Turntable encode (stills → MP4/GIF via FFmpeg) as a separate traced job.
6. P2 — 4K and 4:5 render profiles; Cycles production profile.
7. P2 — FBX/OBJ intake through a converter with the same preflight.
