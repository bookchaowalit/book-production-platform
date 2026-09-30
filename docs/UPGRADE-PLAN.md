# Upgrade Plan

## Current state

Score: 7/10 (was 5/10) — broad, well-tested deterministic adapters; the
full `scripts/check.py` gate now passes on Linux and runs in CI, but Blender,
FreeCAD and Unity paths are only exercised on the Windows developer host.

## Backlog

### P0

- Confirm the first GitHub Actions run is green, especially the graphics
  rendering tests that use the runner's preinstalled Google Chrome.

### P1

- Graphics batches write `<request_id>.svg/.png` straight into the shared
  output directory, so a later batch (different idempotency key) with the same
  `request_id` silently overwrites files an earlier manifest still hashes.
  Scope artifacts per batch (e.g. `<batch_id>/`) or refuse to overwrite files
  whose bytes differ, and update `pipeline/jobs.py` and `export.py` together.
- Add a Windows CI job (`windows-latest`) so the `os.name == "nt"` branches
  (msvcrt locking, publish retries, short paths) run somewhere automatically.
- `data_pipeline/jobs.py` publishes the artifact directory before writing the
  receipt; a crash in between leaves a job that every replay rejects with
  `replay_integrity_error`. Check the other `*_production/jobs.py` for the same
  order, then document the manual recovery or add a repair command.

### P2

- Raise coverage for `graphics_production/export.py`, `audio_production/jobs.py`
  and `graphics_production/cli.py` (currently under 35% without a browser or
  FFmpeg) with stubbed-runtime tests like `tests/graphics/test_jobs.py`.
- Replace `httpx`-backed `fastapi.testclient` usage before Starlette drops it
  (deprecation warning in every run).

## Done in this pass

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
