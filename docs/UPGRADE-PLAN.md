# Upgrade Plan

## Current state

Score: 8.5/10 (8 after pass 2, 7 after pass 1, 5 before) — broad,
well-tested deterministic adapters; data_pipeline, BIM and CAD jobs all
survive a crash between publish and receipt, graphics batches write private
folders, and the audio boundary is checked, but Blender, FreeCAD and Unity
paths are only exercised on the Windows host.

## Backlog

### P0

- Confirm the first GitHub Actions run is green, especially the graphics
  rendering tests that use the runner's preinstalled Google Chrome.

### P1

- Add a Windows CI job (`windows-latest`) so the `os.name == "nt"` branches
  (msvcrt locking, publish retries, short paths) run somewhere automatically.
- Run the browser-marked graphics tests (CI with Chrome, or a local
  Chromium) against the per-batch folder layout; they were updated but only
  the stubbed-browser tests ran in pass 3.
- `spreadsheet_production` and `audio_production` jobs: audit for the same
  publish-then-receipt window (audio writes `.receipt.json` inside the
  published directory, like `game_production`, so check before porting).

### P2

- Archive `book-graphics-production` once nothing depends on it (the parity
  script shows no module exists only there).

- Raise coverage for `graphics_production/export.py`, `audio_production/jobs.py`
  and `graphics_production/cli.py` (currently under 35% without a browser or
  FFmpeg) with stubbed-runtime tests like `tests/graphics/test_jobs.py`.
- Replace `httpx`-backed `fastapi.testclient` usage before Starlette drops it
  (deprecation warning in every run).

## Done in pass 1

- Suite went from 13 failed + 41 errors to green on Linux: BIM real-worker
  tests skip when the pinned Windows FreeCAD runtime is absent (like CAD),
  graphics rendering tests skip without a browser, and the Windows-only
  publish-retry test is split into a Windows retry test and a POSIX
  fail-closed test.
- Strict mypy is pinned to `platform = "win32"` so it gives the same result on
  Linux and Windows.
- Fixed: two items in one graphics batch whose `request_id`s sanitize to the
  same file name (duplicates, case variants, stripped punctuation) overwrote
  each other's artifacts and broke the manifest hashes; the later item is now
  rejected with `artifact_name_collision`.
- Added graphics request-validation tests and browser-free naming tests.
- Added `.github/workflows/ci.yml` running `scripts/check.py` with FFmpeg.

## Done in this pass (pass 2)

- Fixed: a later graphics batch (different idempotency key) reusing a
  `request_id` silently overwrote SVG/PNG files an earlier manifest still
  hashes. Such items are now rejected with
  `artifact_exists_with_different_content`; identical bytes are reused.
- Fixed: a non-object batch item raised `AttributeError` for the whole batch;
  it is now an item-level `invalid_request` rejection.
- Ownership decided and documented (README): this repository owns graphics;
  `book-graphics-production` is a frozen fork that only receives safety fixes.
  Added `scripts/graphics_fork_parity.py` (with tests, in Ruff/mypy gates) to
  report drift and fail if the fork has a module this repository lacks.
  Current drift: `cli`, `jobs` (recipe version only), `layout` (Thai cluster
  wrapping), `main`, `templates` (announcement layout); `export` platform-only.

## Done in pass 2 (data_pipeline crash recovery)

- `data_pipeline/jobs.py`: receipts are written to a private temp file,
  fsynced, then hard-linked into place (fallback: checked `os.replace` under
  the fingerprint lock), so a crash never leaves a truncated receipt.
- Published artifacts without a receipt (crash after the publish rename) are
  recovered on the next run: the job is rebuilt in staging and the receipt is
  written only if every artifact hashes identically; tampered or incomplete
  directories still fail closed with `replay_integrity_error` and are left
  untouched. The result reports `metadata.receipt_recovered: true`.
- Abandoned `.<fingerprint>.staging-*` directories are removed under the lock.
- Tests simulate crashes before the receipt write and during the link, plus
  tampered/incomplete recovery, no-hard-link filesystems and stale staging.

## Done in this pass (pass 3)

- BIM and CAD jobs now recover from a crash between the publish rename and
  the receipt write (was a permanent `takeoff_integrity_error` /
  `replay_integrity_error`). BIM rebuilds and writes the receipt only if the
  rebuild matches every published byte; CAD (not byte-reproducible: the QA
  manifest records timings) derives the receipt from the published bytes and
  requires every replay check, including the manifest's fingerprint, runtime
  and artifact hashes. Results report `receipt_recovered`; inconsistent
  directories fail closed untouched. CAD removes abandoned staging dirs.
- Fixed: a crash mid BIM receipt write left a fixed-name `.tmp` that made
  every later run fail; the temp name is now private. The BIM fingerprint lock
  uses `flock` on POSIX, and BIM job tests use a synthetic IFC with a stubbed
  worker, so 17 of them run on Linux/CI instead of skipping.
- Graphics batches write artifacts to `<output>/<batch_id>/` (manifests record
  `<batch_id>/<name>`), so a later batch reusing a `request_id` or producing
  different PNG bytes is accepted instead of rejected. Export resolves these
  paths safely (rejects `..`, absolute, drive and deep paths) and still reads
  legacy bare names; browser-free export test added.
- Audio ownership documented: not a fork. This repository owns `audio-ir.v1`,
  `book-audio-production` owns `audio-batch.v1` and the FFmpeg pin;
  `scripts/audio_boundary_check.py` (tested, in Ruff/mypy gates) fails if
  either side crosses that line.
