# 3D Product Scene Composer V1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build a local deterministic CLI that turns a bounded SceneIR and one embedded-resource GLB into an editable Blender scene, a verified PNG render and a QA manifest.

**Architecture:** Python validates the versioned contract and GLB before launching Blender. A trusted Blender worker applies a fixed studio recipe, saves a self-contained blend file, then a fresh Blender process reopens it and renders the PNG. The parent process verifies outputs and publishes one content-addressed job directory atomically; standard-path model calls remain zero.

**Tech Stack:** Python 3.12, Pydantic 2.x, Pillow 12.x, pytest 9.x, Blender 5.2.2 LTS background mode. Keep runtime dependencies limited to Pydantic and Pillow; Blender is a separately installed executable.

**Spec:** [3D Product Scene Composer V1A design](../specs/2026-09-23-3d-product-scene-composer-design.md)

## Global Constraints

- Source and tests live in the ignored standalone repository at , on branch main.
- SceneIR version is scene-ir.v1; only one local .glb asset per job; only allowlisted preset IDs are accepted.
- Reject absolute paths, URLs, path traversal, symlinked asset paths, external GLB resources, unknown extensions, animations, skins and unsupported primitive modes.
- Enforce GLB ≤256 MiB, ≤100 imported mesh objects, ≤1,000,000 triangles, each texture side ≤4,096 px, total embedded textures ≤128 MiB, and render profiles square-1024.v1 / landscape-1920x1080.v1.
- Each Blender process has a 300-second deadline; no arbitrary shell/Python code, network fetch, overwrite, LLM, cloud renderer or hidden engine fallback.
- Outputs are exactly scene.blend, render.png, qa-manifest.json; publish only after a fresh Blender process reopens the blend and render QA passes.
- The project catalog accepts only verified GitHub repository URLs and SHAs. Do not invent or create a remote; keep catalog registration pending until an approved, verifiable remote exists.

## Review Focus

- A Windows absolute path, symlink, or traversal input must fail before Blender starts; test each path class.
- A malformed GLB chunk length or declared buffer beyond the BIN chunk must fail as invalid_glb, without a large allocation.
- A GLB with external image/buffer URI, unsupported extension, animation, non-triangle primitive, or texture above caps must fail with a stable code.
- A missing/wrong Blender executable or timed-out worker must leave no accepted artifact and no partial final directory.
- A replay with a changed source hash or tampered output must never reuse the old artifact; an exact replay must reuse verified hashes without rendering again.

---

### Task 1: Create the product boundary and strict SceneIR contract

**Files:**
- Create pyproject.toml, .gitignore, README.md
- Create src/book_production_platform/__init__.py
- Create src/book_production_platform/scene3d/__init__.py
- Create src/book_production_platform/scene3d/models.py
- Create src/book_production_platform/scene3d/errors.py
- Create tests/test_scene_ir.py
- Create scene3d/scene-ir.v1.schema.json

**Interfaces:**
- SceneIR is a Pydantic model with strict extra-field rejection and fields schema_version, asset.path, recipe, composition, lighting, background, material_preset, and render_profile.
- parse_scene_ir(value: object) -> SceneIR returns a validated model or raises SceneInputError whose code is invalid_scene_ir.
- Preset fields are fixed Literal values from the approved design; do not accept free-form Python, node graphs, or renderer IDs from the input.

- [x] **Step 1: Write the failing contract tests**

Add these behavior tests to tests/test_scene_ir.py:

~~~python
def test_scene_ir_accepts_the_allowlisted_minimal_scene():
    parsed = parse_scene_ir({
        "schema_version": "scene-ir.v1",
        "asset": {"path": "product.glb"},
        "recipe": "product-hero.studio.v1",
        "composition": "three-quarter.v1",
        "lighting": "softbox-three-point.v1",
        "background": "studio-neutral.v1",
        "material_preset": "preserve-source.v1",
        "render_profile": "square-1024.v1",
    })
    assert parsed.render_profile == "square-1024.v1"

def test_scene_ir_rejects_unknown_fields():
    with pytest.raises(SceneInputError) as error:
        parse_scene_ir({**valid_scene(), "python": "print(1)"})
    assert error.value.code == "invalid_scene_ir"

def test_scene_ir_rejects_absolute_and_traversal_asset_paths():
    for path in ("C:/private/product.glb", "../product.glb", "https://host/product.glb"):
        with pytest.raises(SceneInputError):
            parse_scene_ir({**valid_scene(), "asset": {"path": path}})
~~~

- [x] **Step 2: Run py -3 -m pytest -q tests/test_scene_ir.py; confirm collection fails because the contract module is absent.**
- [x] **Step 3: Add the Pydantic contract, stable error type, package metadata, strict JSON schema and small fixture helper; do not add processing behavior yet.**
- [x] **Step 4: Run py -3 -m pytest -q tests/test_scene_ir.py; expect all three contract tests to pass.**
- [x] **Step 5: Commit the standalone repository scaffold and contract as feat(scene3d): add strict SceneIR v1 contract.**

### Task 2: Validate GLB structure and resource budgets

**Files:**
- Create src/book_production_platform/scene3d/glb.py
- Modify src/book_production_platform/scene3d/errors.py
- Create tests/helpers/glb_factory.py
- Create tests/test_glb_validation.py

**Interfaces:**
- inspect_glb(path: Path) -> GlbSummary validates the GLB 2.0 header/chunks, core feature allowlist, embedded resources, mesh nodes, triangle count, texture dimensions and byte budgets.
- GlbSummary exposes mesh_object_count, triangle_count, texture_bytes, texture_dimensions, and extensions.
- Rejected assets raise SceneInputError with a stable code: invalid_glb, unsupported_glb_feature, unsafe_glb_resource, or resource_cap_exceeded.

- [x] **Step 1: Add GLB fixtures built from literal JSON chunks and small PNG bytes, then test one valid triangle GLB, bad magic/length, external URI, extension, animation, unsupported primitive mode, triangle cap and texture cap.**
- [x] **Step 2: Run py -3 -m pytest -q tests/test_glb_validation.py; expect failures naming missing inspect_glb.**
- [x] **Step 3: Implement bounded chunk parsing with struct, JSON parsing, buffer-view bounds checks, PNG/JPEG dimension reads and explicit rejection before Blender invocation. Do not trust declared lengths to allocate memory.**
- [x] **Step 4: Run py -3 -m pytest -q tests/test_scene_ir.py tests/test_glb_validation.py; expect the full contract and GLB set to pass.**
- [x] **Step 5: Commit as feat(scene3d): preflight bounded GLB assets.**

### Task 3: Compose and save the Blender scene

**Files:**
- Create src/book_production_platform/scene3d/blender_worker.py
- Create src/book_production_platform/scene3d/scene_builder.py
- Create tests/test_blender_scene.py
- Create tests/fixtures/README.md

**Interfaces:**
- build_scene(scene_ir_path: Path, asset_path: Path, blend_path: Path) -> None is the worker build mode.
- render_saved_scene(blend_path: Path, png_path: Path) -> None is the worker render mode and must reopen the blend file from disk before rendering.
- Worker modes accept only file paths and serialized validated IR; input is never evaluated as code.

- [x] **Step 1: Write a Blender integration test that creates a tiny GLB from glb_factory, invokes Blender background build mode, and asserts a non-empty blend file exists.**
- [x] **Step 2: Run py -3 -m pytest -q tests/test_blender_scene.py; if Blender is absent, confirm the test reports the exact runtime prerequisite rather than a false pass.**
- [x] **Step 3: Implement GLB import, deterministic bounding-box centering/uniform fit, neutral studio background, three-quarter camera, three-point area lighting, selected render settings, packed images and blend save.**
- [x] **Step 4: Extend the integration test to invoke fresh-process render mode and assert a valid PNG with the exact profile dimensions. Run on the pinned Blender build.**
- [x] **Step 5: Commit as feat(scene3d): compose editable Blender product scenes.**

### Task 4: Add isolated jobs, replay, manifest and output QA

**Files:**
- Create src/book_production_platform/scene3d/jobs.py
- Create src/book_production_platform/scene3d/qa.py
- Create src/book_production_platform/scene3d/runtime.py
- Create tests/test_scene_jobs.py
- Create tests/test_scene_qa.py

**Interfaces:**
- compose_scene(scene_path: Path, input_root: Path, output_root: Path, blender_bin: Path, timeout_s: int = 300) -> JobResult validates, fingerprints, runs build and fresh-process render, verifies and atomically publishes.
- verify_png(path: Path, expected_size: tuple[int, int]) -> None loads the image, checks exact dimensions and rejects zero-byte/flat renders.
- JobResult includes status, fingerprint, artifact_dir, artifact_hashes, replay, and model_calls.

- [x] **Step 1: Write tests for accepted artifacts, exact replay (second invocation does not render), changed-source fingerprint, tampered replay rejection, timeout cleanup and no-overwrite behavior.**
- [x] **Step 2: Run py -3 -m pytest -q tests/test_scene_jobs.py tests/test_scene_qa.py; confirm each failure identifies its missing observable behavior.**
- [x] **Step 3: Implement content fingerprinting from canonical IR, source SHA-256, composer/recipe version, exact Blender build and render profile; use a per-fingerprint lock, same-volume staging, bounded subprocess timeouts, manifest hashes and atomic directory publication.**
- [x] **Step 4: Run the job/QA tests and Blender integration test; expect accepted outputs to contain exactly the three specified artifacts and replay hashes to match.**
- [x] **Step 5: Commit as feat(scene3d): publish verified idempotent scene jobs.**

### Task 5: Deliver the CLI, 20-case acceptance suite and benchmark evidence

**Files:**
- Create src/book_production_platform/cli.py
- Create src/book_production_platform/scene3d/benchmark.py
- Create tests/test_cli.py
- Create tests/fixtures/scene3d/scene_cases.json
- Modify README.md

**Interfaces:**
- book-production scene compose --scene <json> --input-root <dir> --output-root <dir> --blender <exe> exits 0 only for an accepted/replayed artifact, and returns stable JSON status/errors.
- book-production scene benchmark --fixtures <dir> --output <json> runs 20 deterministic cases and reports accepted/rejected, QA failures, fresh/replay latency, bytes and model_calls.
- Human golden review uses five named PNG renders; automated checks do not report aesthetic quality.

- [x] **Step 1: Test CLI help, invalid SceneIR exit code, accepted JSON output and unsupported runtime diagnostics.**
- [x] **Step 2: Run py -3 -m pytest -q tests/test_cli.py; confirm expected commands fail before CLI registration exists.**
- [x] **Step 3: Implement the argparse entry point, benchmark case loader and Thai-first product README with install/runtime, input, output, limits, error and replay examples.**
- [x] **Step 4: Run py -3 -m pytest -q; run the full 20-case benchmark, compare EEVEE and Cycles candidate profiles on golden fixtures, inspect five renders, and record the selected profile and timings without claiming a dollar cost.**
- [x] **Step 5: Commit as feat(scene3d): add CLI and pilot acceptance benchmark.**

**Completion gate passed locally:** the post-review package suite passed 49 tests; one host-dependent symlink test was skipped because this Windows host denies symlink creation. The 20-case suite previously matched its expected counts with no unexpected result and zero model calls; five upright renders were visually inspected; and an installed CLI created and replayed a Blender artifact while a fresh Blender process reopened and rendered its `.blend`. The final receipt/parser-only hardening was verified by unit tests; the Windows Store Blender package denied direct headless process access on the final rerun. The product repository has no remote, so no push or registry entry was fabricated.

**Implementation outcome:** commits `14c087e`, `467ace0`, `cec19bf`, `780a049`, `5caf90a`, `b91fb90` and `0f0eb77` cover the contract, GLB preflight, Blender scene creation, verified jobs, CLI, pilot benchmark, nested-input hardening and manifest integrity receipts. On this host EEVEE 64-sample reopen-and-render averaged 2.316 s for square and 3.300 s for landscape; the Cycles CPU 32-sample candidate averaged 10.356 s and 20.711 s. This is a synthetic local pilot; representative customer assets and an OS-level hostile-file sandbox remain future work.
