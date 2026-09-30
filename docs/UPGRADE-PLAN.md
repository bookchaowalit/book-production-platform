# Upgrade Plan

## Current state

Score: 7.5/10 (was 7/10 after pass 1, 5/10 before) — broad, well-tested
deterministic adapters; graphics batches can no longer break each other's
manifests and graphics ownership is documented with a drift check, but
Blender, FreeCAD and Unity paths are only exercised on the Windows host.

## Backlog

### P0

- Confirm the first GitHub Actions run is green, especially the graphics
  rendering tests that use the runner's preinstalled Google Chrome.

### P1

- Add a Windows CI job (`windows-latest`) so the `os.name == "nt"` branches
  (msvcrt locking, publish retries, short paths) run somewhere automatically.
- `data_pipeline/jobs.py` publishes the artifact directory before writing the
  receipt; a crash in between leaves a job that every replay rejects with
  `replay_integrity_error`. Check the other `*_production/jobs.py` for the same
  order, then document the manual recovery or add a repair command.

- Graphics now fails closed (`artifact_exists_with_different_content`) when a
  later batch would change a file in the shared output directory. If PNG
  bytes from the local browser turn out not to be reproducible run-to-run,
  switch to per-batch artifact directories (`<batch_id>/`), updating
  `pipeline/jobs.py` and `export.py` (which joins by bare file name) together.
- `audio_production` duplicates `book-audio-production` the same way graphics
  duplicated `book-graphics-production`; decide the owner and add a drift check
  like `scripts/graphics_fork_parity.py`.

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
