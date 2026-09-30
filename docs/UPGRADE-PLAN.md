# Upgrade Plan

## Current state

Score: 8/10 (7.5 after pass 2 graphics work, 7 after pass 1, 5 before) —
broad, well-tested deterministic adapters; graphics batches can no longer
break each other's manifests and data_pipeline now survives a crash between
publish and receipt, but Blender, FreeCAD and Unity paths are only exercised
on the Windows host.

## Backlog

### P0

- Confirm the first GitHub Actions run is green, especially the graphics
  rendering tests that use the runner's preinstalled Google Chrome.

### P1

- Add a Windows CI job (`windows-latest`) so the `os.name == "nt"` branches
  (msvcrt locking, publish retries, short paths) run somewhere automatically.
- `bim_production/jobs.py` (`_rename_directory` then `_write_receipt`) and
  `cad_production/jobs.py` (`_publish` then `_write_receipt_exclusive`) have
  the same publish-then-receipt window data_pipeline had. Port the
  data_pipeline recovery (rebuild, compare hashes, then write the receipt;
  clean `.staging-*` for the fingerprint under the lock). `game_production`
  keeps its receipt inside the published directory and is not affected.

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
