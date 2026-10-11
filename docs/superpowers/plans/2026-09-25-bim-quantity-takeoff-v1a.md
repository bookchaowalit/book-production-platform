---
title: BIM Quantity Takeoff V1A — Implementation Plan
aliases: [BIM Takeoff Plan, IFC QTO V1A]
tags: [plan, product, bim, ifc, quantity-takeoff]
created: 2026-09-25
updated: 2026-09-25
status: approved-implementation-plan
description: A bounded implementation plan for local, deterministic IFC quantity takeoff with provenance and replay verification.
related:
  - ../specs/2026-09-25-bim-quantity-takeoff-design.md
  - 2026-09-22-production-platform-next-products.md
---

# BIM Quantity Takeoff V1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use the previously selected Native execution method with `superpowers:executing-plans` to implement this plan task-by-task on the owning repositories' `main` branches. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local BIM Quantity Takeoff adapter that reads a strict `BimTakeoffIR` and one IFC2X3/IFC4 model, then emits a provenance-preserving JSON/CSV schedule and QA manifest with deterministic coverage findings and integrity-checked replay.

**Architecture:** Keep the implementation in the nested `book-production-platform` product repository. Python 3.12 validates requests, paths, jobs, and outputs; a bounded FreeCADCmd/Python 3.11 worker imports the already-present IfcOpenShell 0.8.4 through the verified Windows short path, parses and schema-validates IFC without EXPRESS where-rules, extracts only direct occurrence-level QTOs, and returns bounded structured records. Pure Python normalizes units, aggregates exact semantic keys, validates artifacts, and publishes content-addressed jobs atomically. The standard path has `model_calls=0` and no network activity.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, Ruff, strict mypy, FreeCAD 1.1.3 / Python 3.11, IfcOpenShell 0.8.4, Windows x86-64.

**Spec:** [BIM Quantity Takeoff V1A design](../specs/2026-09-25-bim-quantity-takeoff-design.md)

All product paths below are relative to ``. The root Solo Empire repository owns only the spec, plan, roadmap/catalog updates, and summarized evidence.

## Global Constraints

- The implementation belongs in the existing `book-production-platform` repository. The root Solo Empire repository owns this spec, plan, roadmap/catalog entries, and summary evidence; it must not copy IFC fixtures, source code, or generated takeoff artifacts from the product runtime.
- The V1A pilot is limited to the tested local Windows x86-64 runtime. Treat input as trusted local project data, not hostile uploads. Bound the worker to 120 seconds, IFC input to 64 MiB, and the parsed model to 250,000 IFC instances; use `shell=False`, closed stdin, a private temporary working directory, bounded diagnostics, and stable error codes. No OS-level sandbox is claimed.
- Use the verified short `site-packages` path and keep every loaded module path below the Windows path limit; fail with a stable runtime-path error if a usable short path is unavailable. Do not copy or install a second runtime or add dependencies. Use `ifcopenshell.open()` for syntax parsing and schema validation with `express_rules=False`; do not claim EXPRESS where-rule coverage.
- Do not resolve or execute embedded text, scripts, macros, URLs, linked resources, or external references in an IFC file. Disable network access for the worker.
- Keep the staged IFC input copy, worker temporary files, manifests, and outputs local under the product's ignored `.runtime/bim/` area. Never upload a model or send it to an external parser/provider. Never modify the user's source IFC.
- The source IFC path is excluded from canonical IR, manifests, benchmark summaries, and logs. Do not include local paths or raw source-file excerpts in reports or telemetry.
- Extract only explicit occurrence-level `IfcElementQuantity` values for `IfcQuantityLength`, `IfcQuantityArea`, `IfcQuantityVolume`, and `IfcQuantityCount`. Do not inherit type quantities or infer values from geometry, project names, numeric magnitude, or display settings.
- Normalize supported dimensionally matching physical units to SI; count is unitless. Keep provenance on every quantity row and aggregate only exact semantic keys, including method of measurement. Missing or unsupported values are coverage gaps, never zeroes or estimates.
- Publish exactly `takeoff.json`, `takeoff.csv`, and `qa-manifest.json` after all validation and QA succeed. Publish atomically, never overwrite an existing job, and verify the receipt and all three artifact bytes on replay.
- The standard route uses deterministic Python/IfcOpenShell logic and reports `model_calls=0`. Monetary cost is `unknown` without a local compute rate card.
- No service, API, database migration, cloud job, external message, deployment, pricing, code compliance, or destructive IFC change is in scope. Rollback disables the `bim` CLI route and retains already published local jobs.

## Review Focus

- The wrapper path is long, `GetShortPathNameW` returns an unusable path, a native DLL cannot load, or a pinned hash/version changes; Task 0 must fail closed with `bim_runtime_path_unsupported` or `unsupported_ifcopenshell_runtime` before takeoff artifacts are created.
- Duplicate JSON keys, numeric/boolean values in string or enum fields, query-like or unknown fields, non-finite JSON constants, and IR byte-limit edges must be rejected before FreeCADCmd starts; cover in Task 1.
- IFC-SPF parse errors, unsupported IFC4X3, schema findings, missing/duplicate selected GlobalIds, malformed unit assignments, type-only QTOs, and the 250,000-instance boundary must produce stable fatal errors without a published job; cover in Task 2.
- Quantity-unit edge cases, property-unit precedence over project defaults, unit-dimension mismatch, duplicate names, unsupported kinds, gross/net or method-key collisions, and no-quantity `needs_input` must never silently produce an incorrect value; cover in Task 3.
- Interrupted publication, pre-existing output directories, absent/forged receipts, modified or missing artifacts, exact replay, and concurrent identical jobs must not overwrite or accept tampered data; cover in Task 5.

## Implementation Tasks

### Task 0: Reproduce the pinned IfcOpenShell runtime probe inside FreeCADCmd

**Files:**

- Create `src/book_production_platform/bim_production/__init__.py`.
- Create `src/book_production_platform/bim_production/errors.py`.
- Create `src/book_production_platform/bim_production/runtime.py`.
- Create `src/book_production_platform/bim_production/worker.py` for the fixed worker protocol and in-process preflight entry point.
- Create `src/book_production_platform/bim_production/freecad_worker.FCMacro`.
- Create `src/book_production_platform/bim_production/ifcopenshell-runtime.v1.json` with the verified FreeCADCmd SHA-256 `8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23`, wrapper SHA-256 `C505947C7D8C1342195498A0E48F38A5DA35581104DF8B5556F930FBCF297B78`, FreeCAD 1.1.3 build, Python 3.11, IfcOpenShell 0.8.4, platform, and worker protocol.
- Create `tests/bim_production/__init__.py`, `tests/bim_production/test_runtime.py`, and `tests/bim_production/test_worker.py`.
- Add `bim_production/*.FCMacro` and `bim_production/*.json` to `pyproject.toml` package data.

**Interfaces:**

- `BimProductionError(code: str)` exposes only a stable error code.
- `inspect_bim_runtime(freecadcmd: Path, ifc_site_packages: Path) -> BimRuntimeIdentity` verifies the locked FreeCAD executable/build, wrapper hash, Python/IfcOpenShell versions, Windows x86-64 platform, and regular non-link paths.
- `run_bim_worker(freecadcmd: Path, ifc_site_packages: Path, mode: Literal["preflight", "takeoff", "fixture"], request_path: Path, work_root: Path, timeout_s: int) -> BimWorkerResult` uses `shell=False`, `stdin=DEVNULL`, a private short-CWD workspace, a 1–120 second deadline, and bounded diagnostic/result bytes.
- The macro invokes a fixed package worker entry point. It never evaluates commands or scripts supplied in the IFC or request.
- Worker bootstrap calls Windows `GetShortPathNameW` on the existing `site-packages` directory, confirms the resulting wrapper/module paths are below 260 characters, adds that path before importing, uses the existing runtime DLL directory, and sets `sys.dont_write_bytecode=True`. It returns `bim_runtime_path_unsupported` if no usable short path exists.
- `preflight` imports IfcOpenShell in the actual FreeCADCmd process and verifies version/hash, IFC2X3 and IFC4 schema factories, `ifcopenshell.open`, schema validation with `express_rules=False`, direct QTO access, and unit lookup. It does not install or copy runtime files.
- `fixture` is a test-only, declarative fixture-generation mode with a strict allowlist and no user-facing CLI route; it writes only synthetic IFC files under ignored `.runtime/bim/`.

- [ ] **Step 1: Add runtime identity and failure-path tests first**

Test missing executable/site-packages, symlink/junction, wrong FreeCAD version/build/hash, wrong wrapper hash/version, invalid short-path return, module path still at least 260 characters, worker nonzero exit, malformed/oversized worker result, and timeout. Assert stable codes and that no final artifact or receipt exists.

```python
def test_worker_rejects_unusable_windows_short_path(runtime_probe, tmp_path):
    result = runtime_probe(short_path=None, output_root=tmp_path)
    assert result.error_code == "bim_runtime_path_unsupported"
    assert not list(tmp_path.glob("jobs/*"))
```

Run `py -3.12 -m pytest -q tests/bim_production/test_runtime.py tests/bim_production/test_worker.py` and confirm the new behavior tests fail before implementation.

- [ ] **Step 2: Implement the pinned runtime lock and parent preflight**

Load the strict packaged lock, hash the FreeCADCmd and native wrapper in chunks, check exact version/build/platform, reject link components and overlong paths, and return a privacy-safe `BimRuntimeIdentity`. Reuse the same approved runtime files; do not change the CAD runtime lock or add a package dependency.

- [ ] **Step 3: Implement the FreeCADCmd worker bootstrap and bounded runner**

Resolve the verified short path before importing IfcOpenShell, apply `os.add_dll_directory` to the existing runtime directory, disable bytecode writes, run the macro with a fixed argument protocol, and cap diagnostics/result JSON. Terminate the process at timeout and translate failures to stable codes without including source paths or IFC contents.

- [ ] **Step 4: Run the real in-process runtime preflight**

Run the existing portable FreeCADCmd with the BIM worker's `preflight` mode. Assert its reported version and hashes match the lock, both schema factories load, `ifcopenshell.open()` rejects a malformed synthetic SPF, QTO/unit functions are callable, schema validation reports zero findings on a valid tiny IFC4 model with `express_rules=False`, and no GUI module is loaded. If any verified capability fails, stop and return to spec review rather than installing a replacement runtime.

- [ ] **Step 5: Run focused tests and static checks**

Run `py -3.12 -m pytest -q tests/bim_production/test_runtime.py tests/bim_production/test_worker.py`, `py -3.12 -m ruff check src/book_production_platform/bim_production tests/bim_production`, and `py -3.12 -m mypy --strict src/book_production_platform/bim_production`.

### Task 1: Define strict BimTakeoffIR and canonical fingerprints

**Files:**

- Create `src/book_production_platform/bim_ir/__init__.py`, `src/book_production_platform/bim_ir/models.py`, and `src/book_production_platform/bim_ir/bim-takeoff.v1.schema.json`.
- Create `tests/bim_ir/__init__.py`, `tests/bim_ir/test_models.py`, and `tests/bim_ir/test_schema.py`.
- Add `bim_ir/*.json` to `pyproject.toml` package data.

**Interfaces:**

- `BimTakeoffIR` is frozen/strict and has exactly `schema_version="bim-takeoff.v1"`, `takeoff_id`, `element_classes`, and `output_units="SI"`.
- `parse_bim_takeoff_ir(value: object) -> BimTakeoffIR`, `load_bim_takeoff_ir(path: Path) -> BimTakeoffIR`, and `canonical_bim_takeoff_ir(ir: BimTakeoffIR) -> bytes` enforce the schema, duplicate-key rejection, UTF-8, finite JSON, byte limit, safe ID, unique nonempty allowlisted classes (`IfcWall`, `IfcSlab`, `IfcColumn`, `IfcBeam`, `IfcDoor`, `IfcWindow`), and fixed SI policy.
- `BimTakeoffIRError(code: str)` always uses `invalid_bim_takeoff_ir`; no query, expression, unit formula, URL, path, or script field is accepted.

- [ ] **Step 1: Write IR contract tests**

Cover a valid minimal request, all supported class values, unknown and missing fields, duplicate keys, invalid UTF-8, invalid IDs, duplicate/empty classes, unsupported class/output units, numeric/boolean class entries, NaN/Infinity, oversized input, and canonical key-order invariance. Run the IR tests and confirm missing APIs fail.

- [ ] **Step 2: Implement Pydantic models and strict bounded JSON loading**

Use the repository's existing Pydantic 2 pattern from `cad_ir`; keep decimal quantity values out of the request because V1A reads them only from IFC. Canonical JSON is UTF-8, key-sorted, compact, finite, and ends with one newline.

- [ ] **Step 3: Add and cross-check the versioned JSON schema**

Write an equivalent Draft 2020-12 schema, load it as package data in the schema test, and check the valid/invalid examples agree with the Python validator.

- [ ] **Step 4: Run focused IR and type checks**

Run `py -3.12 -m pytest -q tests/bim_ir`, `py -3.12 -m ruff check src/book_production_platform/bim_ir tests/bim_ir`, and `py -3.12 -m mypy --strict src/book_production_platform/bim_ir`.

### Task 2: Validate IFC syntax/schema and create synthetic test models

**Files:**

- Add bounded IFC parsing/identity logic to `src/book_production_platform/bim_production/worker.py` and the fixed bootstrap in `freecad_worker.FCMacro`.
- Create `tests/bim_production/fixture_worker.FCMacro`, `tests/bim_production/fixtures.py`, `tests/bim_production/test_ifc_validation.py`, and descriptor-only fixtures under `tests/fixtures/bim/`.
- Store generated IFCs only under the ignored product path `.runtime/bim/test-fixtures/`; do not commit generated IFC data.

**Interfaces:**

- `validate_ifc_spf(model_path: Path, expected_schemas: frozenset[str], max_instances: int = 250_000) -> IfcValidationSummary` runs only inside the pinned worker, returning schema, project identity hash, total instance count, selected element count, and bounded findings.
- `open_validate_ifc()` uses `ifcopenshell.open()` for syntax, accepts only IFC2X3/IFC4 after lock preflight, and invokes `ifcopenshell.validate.validate(..., express_rules=False)`. It rejects parser/schema findings, schema mismatch, malformed unit assignments, over-limit models, and missing/duplicate GlobalIds on selected elements.
- All input is copied byte-for-byte to a private `.runtime/bim/worker/` staging area after parent checks; the worker parses that copy, never writes to the source, and the parent confirms the copied SHA-256 matches the source hash.
- The test fixture macro accepts only `bim-fixture.v1` declarative cases and creates tiny IFC2X3/IFC4 files with explicit units, selected classes, direct occurrence-level QTOs, type-only quantities, and controlled malformed cases. It does not expose Python execution through user input.

- [ ] **Step 1: Write parser/identity integration tests against generated IFC cases**

Test valid IFC2X3 and IFC4; malformed SPF; IFC4X3 rejection; schema findings; count just below, at, and above 250,000 using bounded generator logic or patched count; duplicate/missing selected GlobalIds; malformed unit assignments; and source hash unchanged after run. Verify parser diagnostics do not contain the input path or raw IFC text.

- [ ] **Step 2: Implement only the constrained synthetic fixture generator**

Create the fixture worker macro with fixed schema/class/unit/QTO controls, use `ifcopenshell.file(schema=...)` and `write()` inside FreeCADCmd, and write outputs only beneath the ignored runtime fixture root. Validate descriptor keys and count limits before constructing entities.

- [ ] **Step 3: Implement IFC syntax, schema, identity, and model-size gates**

Open staged files with `ifcopenshell.open()`, check `model.schema`, call schema validation with `express_rules=False`, bound instance count before class traversal, inspect only requested classes, and reject selected elements without valid unique `GlobalId`. Never run EXPRESS where-rules or geometry calculations.

- [ ] **Step 4: Run real IFC validation integration tests**

Run `py -3.12 -m pytest -q tests/bim_production/test_ifc_validation.py` against the pinned worker. Assert the worker reports `gui_loaded=false`, source bytes remain unchanged, and rejected inputs publish no job artifacts or receipts.

- [ ] **Step 5: Run focused static checks**

Run Ruff and strict mypy on the changed BIM production code and tests; avoid importing IfcOpenShell in Python 3.12 test collection, since it exists only inside the locked FreeCAD Python 3.11 runtime.

### Task 3: Extract direct occurrence QTOs, normalize SI, and report coverage

**Files:**

- Create `src/book_production_platform/bim_production/units.py` and `src/book_production_platform/bim_production/extract.py`.
- Extend `src/book_production_platform/bim_production/worker.py` with the `takeoff` operation.
- Create `tests/bim_production/test_units.py` and `tests/bim_production/test_extract.py`.

**Interfaces:**

- `resolve_source_unit(model: object, quantity: object) -> UnitMetadata | CoverageGap` runs inside FreeCADCmd, resolves an explicit quantity unit before the project default through the verified IfcOpenShell APIs, validates dimension, and returns only bounded unit metadata and a decimal-string scale factor.
- `normalize_quantity(source_value: int | float, measure_kind: str, unit: UnitMetadata | None) -> NormalizedQuantity | CoverageGap` runs in the Python 3.12 parent, rejects booleans/non-finite values, applies Decimal conversion, and returns a canonical SI value/unit (`m`, `m2`, `m3`, or `count`). `IfcQuantityCount` is unitless.
- `extract_occurrence_quantities(model: object, ir: BimTakeoffIR) -> ExtractionResult` traverses direct `IfcRelDefinesByProperties` relationships whose property definition is `IfcElementQuantity`; it returns bounded typed source records plus provenance, and does not inspect type-object relationships or geometry.
- A quantity row carries `GlobalId`, actual IFC class, QTO name, quantity name, measure kind, optional method of measurement, normalized decimal value, and SI unit. It preserves the source QTO/name/method without copying unrelated IFC properties or file excerpts.
- Duplicate `(GlobalId, QTO name, quantity name)` matches are omitted and become `quantity_ambiguous`. Missing QTOs, unsupported quantity kinds, unresolved units, and unsupported units produce the stable coverage reasons from the spec. Invalid/nonfinite numeric values or a malformed project unit assignment fail the whole job.
- Aggregate key is exactly `(IFC class, QTO name, quantity name, measure kind, SI unit, method of measurement)`; Decimal addition preserves deterministic serialized values. Report status is `complete` only with no gaps, `partial` when at least one supported row exists with gaps, and `needs_input` when no supported row exists.

- [ ] **Step 1: Add unit conversion tests before implementation**

Use generated IFC2X3/IFC4 models to check metre/millimetre project defaults, explicit quantity-unit precedence, area/volume powers, count, dimension mismatch, no unit, unsupported unit, and exact decimal serialization. Confirm tests fail while the normalization API is absent.

- [ ] **Step 2: Add extraction and coverage tests**

Cover each of the four supported quantity entities; IFC subclasses; occurrence-level versus type-only QTO; duplicate quantity names; absent QTO; malformed numeric values; unsupported measure types; exact aggregate-key separation for QTO names/methods/gross-net values; `partial` and `needs_input`; and zero geometry API calls.

- [ ] **Step 3: Resolve source units in the worker and normalize in the parent**

Call only the verified IfcOpenShell unit APIs inside the worker to resolve explicit quantity units before project defaults and emit a bounded decimal-string conversion factor plus dimension metadata. In Python 3.12, convert the finite source value and factor with `Decimal(str(value))`; reject dimension mismatch and never infer from unit names or magnitudes. Return a coverage gap when a physical unit is absent/unsupported, but fail on malformed unit assignments.

- [ ] **Step 4: Implement occurrence-only quantity extraction and exact-key aggregates**

Read only direct occurrence relations, keep inheritance off, classify the four explicit quantity entity types, omit all ambiguous duplicates, aggregate with the full exact key, and emit stable coverage records without treating gaps as zero.

- [ ] **Step 5: Verify IFC2X3/IFC4 totals through the actual worker**

Run `py -3.12 -m pytest -q tests/bim_production/test_units.py tests/bim_production/test_extract.py`; compare expected per-element values and aggregate totals against hand-authored synthetic fixture expectations, including millimetre-to-metre, square-metre, cubic-metre, and count cases.

- [ ] **Step 6: Run focused Ruff and strict mypy**

Run static checks over `bim_ir`, `bim_production`, and their tests. Keep IFC library objects behind `object`/narrow protocols or worker-local `Any`; do not add IfcOpenShell to root Python dependencies.

### Task 4: Produce deterministic JSON/CSV and a privacy-safe QA manifest

**Files:**

- Create `src/book_production_platform/bim_production/reports.py` and `src/book_production_platform/bim_production/qa.py`.
- Create `tests/bim_production/test_reports.py` and `tests/bim_production/test_qa.py`.
- Add `examples/bim/bim-takeoff.json`.

**Interfaces:**

- `build_takeoff_report(ir: BimTakeoffIR, source_sha256: str, ifc_summary: IfcValidationSummary, extraction: ExtractionResult) -> dict[str, object]` emits schema version, content hash, validation summary, per-element rows, exact-key aggregates, status, and coverage gaps.
- `write_takeoff_csv(rows: Sequence[QuantityRow], path: Path) -> None` writes stable UTF-8 CSV with fixed columns/order, RFC-compatible quoting, SI decimal strings, and no source path.
- `verify_takeoff_artifacts(staging: Path) -> BimQAResult` checks exact allowed filenames, JSON/CSV row parity, schema and hash consistency, count/status invariants, `model_calls=0`, `monetary_cost="unknown"`, and path/raw-source absence.
- `qa-manifest.json` contains schema/runtime identity, input content hash, IFC schema/instance counts, complete/partial coverage counts/reasons, and pre-manifest `takeoff.json`/`takeoff.csv` byte lengths and SHA-256 hashes. The external receipt covers all three final artifacts.

- [ ] **Step 1: Write report/CSV golden tests**

Assert stable JSON bytes across key order and repeated runs; stable CSV headers, order, escaping, Unicode and decimal formatting; exact parity with per-element rows; omitted gaps are not zero; expected status/aggregate totals; and no local path, raw IFC line, address, or unrequested metadata appears.

- [ ] **Step 2: Implement report, CSV, and manifest builders**

Use canonical compact JSON and a fixed CSV schema. Keep the source IFC path out of function inputs where possible; pass only the content hash and schema summary to report builders.

- [ ] **Step 3: Implement staged artifact QA**

Reject duplicate keys, invalid/nonfinite JSON, altered row parity, incorrect aggregate totals, missing/extra files, empty/oversized artifacts, mismatched hashes, status inconsistency, and nonzero model calls before a job can publish.

- [ ] **Step 4: Run focused report and QA checks**

Run `py -3.12 -m pytest -q tests/bim_production/test_reports.py tests/bim_production/test_qa.py`, then Ruff and strict mypy for the same paths.

### Task 5: Add atomic content-addressed jobs and integrity-checked replay

**Files:**

- Create `src/book_production_platform/bim_production/jobs.py`.
- Create `tests/bim_production/test_jobs.py`.
- Keep all job state, staging, locks, receipts, and artifacts under ignored `.runtime/bim/`.

**Interfaces:**

- `build_bim_takeoff_job(ir_path: Path, ifc_path: Path, output_root: Path, freecadcmd: Path, ifc_site_packages: Path, timeout_s: int = 120) -> BimJobResult` validates before launching the worker, copies the source IFC to private staging, hashes it, runs validation/extraction, builds and verifies artifacts, then publishes once.
- Fingerprint hashes source bytes, canonical IR, detected IFC schema, recipe version, parser/runtime identity, and report schema version; it excludes all local paths.
- Jobs publish to `jobs/<fingerprint>/`; receipts are private under `.receipts/<fingerprint>.json`; per-fingerprint locks prevent concurrent duplicate publication.
- An exact replay reopens a bounded receipt, checks runtime/fingerprint, exact artifact-name set, byte lengths and SHA-256 for each of the three files, and returns `replayed` without starting FreeCADCmd. Any mismatch or pre-existing job without a valid receipt returns `takeoff_integrity_error`; it never repairs or overwrites the prior directory.
- `needs_input` is returned without publishing an accepted job. Failed parse, validation, worker timeout, unit fatality, QA, or publication leaves no receipt and no final job directory.

- [ ] **Step 1: Add job lifecycle tests first**

Test fresh complete and partial publish, `needs_input` without publish, exact replay without invoking the worker, source changed/IR changed/runtime lock changed fingerprints, missing/forged/tampered receipt, altered/missing/extra artifacts, pre-existing output no-overwrite, failed-worker cleanup limited to this job's staging, timeout, and concurrent identical requests. Assert prior accepted bytes never change.

- [ ] **Step 2: Implement safe roots, bounded source staging, lock, and fingerprint**

Reject symlink/junction components, non-regular input, wrong extension, oversized IFC/IR, invalid output root, and unsafe pre-existing target before process start. Copy at most 64 MiB to staging, verify source/copy hashes, and use an OS file lock with a 15-second acquisition deadline.

- [ ] **Step 3: Implement receipt verification and replay before worker launch**

Strictly parse duplicate-free receipt JSON, compare the expected content/runtime fingerprint, exact artifact set, hashes and sizes, and fail closed. Add a test spy proving exact replay starts no child process and reports `model_calls=0`.

- [ ] **Step 4: Implement staged build, QA, and atomic publication**

Build the two report artifacts in same-volume staging, create and verify the manifest, hash all three files into the receipt, atomically rename the new job directory, then atomically write the receipt. Cleanup only the current temporary staging directory on failure; preserve every pre-existing final path.

- [ ] **Step 5: Run lifecycle, failure, and concurrency tests**

Run `py -3.12 -m pytest -q tests/bim_production/test_jobs.py tests/bim_production/test_qa.py`; check the generated runtime area manually to confirm transient requests/IFC copies remain ignored and accepted outputs contain exactly three files.

- [ ] **Step 6: Run focused static checks**

Run Ruff and strict mypy across all BIM source and tests added so far.

### Task 6: Wire the local CLI, 20-case acceptance benchmark, and operating guide

**Files:**

- Create `src/book_production_platform/bim_production/cli.py` and `src/book_production_platform/bim_production/benchmark.py`.
- Update `src/book_production_platform/cli.py`, `scripts/check.py`, `pyproject.toml`, and `README.md`.
- Create `tests/bim_production/test_cli.py`, `tests/bim_production/test_benchmark.py`, `tests/fixtures/bim/cases.json`, and `examples/bim/bim-takeoff.json`.

**Interfaces:**

- `book-production bim takeoff --ir PATH --ifc PATH --output-root PATH --freecadcmd PATH --ifc-site-packages PATH --timeout-s 120` prints stable JSON. Exit 0 means `complete` or `replayed`; exit 3 means `partial`; exit 4 means `needs_input`; exit 2 means rejected.
- `book-production bim benchmark --fixtures tests/fixtures/bim/cases.json --output .runtime/bim/benchmarks/latest.json --freecadcmd PATH --ifc-site-packages PATH --timeout-s 120` runs 20 cases: five fresh jobs (four complete and one partial), their five exact replays, eight expected rejects, and two tamper replays.
- Fixture descriptors only contain synthetic IFC creation parameters and expected values/error codes. The benchmark summary contains IDs, outcomes, latencies, output byte counts, error codes, and model-call totals; it excludes file paths, raw IFC content, building names, and private metadata.
- Top-level `book-production` dispatch delegates BIM arguments to `bim_production.cli.main()` without implementing BIM parsing in the platform dispatcher.
- README documents supported input/schema/quantity behavior, required local runtime paths, output artifacts, exit semantics, a no-upload privacy note, limitations, and exact offline checks.

- [ ] **Step 1: Write CLI and benchmark tests before wiring**

Test command help, malformed arguments, stable error JSON, every status/exit code, root CLI delegation, unsupported runtime, 20-case counts, expected totals, no worker start for invalid IR/path, `model_calls=0`, `monetary_cost="unknown"`, and path/privacy redaction. Confirm API-missing tests fail first.

- [ ] **Step 2: Implement the BIM CLI and top-level dispatch**

Parse arguments in the domain CLI, call the job API, emit stable sorted JSON, map stable errors and partial/needs-input states to documented exits, and add a single `bim` remainder dispatcher to `src/book_production_platform/cli.py`.

- [ ] **Step 3: Implement fixture registry validation and the 20-case benchmark**

Validate exact fixture IDs/counts and expected error codes, run the five fresh jobs, five replays, eight rejects, and two tamper attempts, compare every expected row/aggregate total, stop on mismatch, and write one bounded summary under `.runtime/bim/benchmarks/`.

- [ ] **Step 4: Add examples, package data, check targets, and README instructions**

Add the example IR and runtime lock/worker resources to package data; extend repository `scripts/check.py` BIM Ruff/mypy targets; document commands using runtime files already present; do not add installation instructions that download or install another IfcOpenShell runtime.

- [ ] **Step 5: Run CLI/benchmark unit tests and targeted static checks**

Run `py -3.12 -m pytest -q tests/bim_production/test_cli.py tests/bim_production/test_benchmark.py`, Ruff, and strict mypy over BIM sources/tests and top-level CLI.

### Task 7: Run the full synthetic acceptance gate and record bounded evidence

**Files:**

- Update `README.md` only for findings that are relevant to operating the product.
- Update the root [production-platform roadmap](2026-09-22-production-platform-next-products.md) with a concise status/evidence link only after the product commit/evidence exists.
- Create `docs/superpowers/reviews/2026-09-25-bim-quantity-takeoff-v1a-evaluation.md` in the root repository with summary metrics and limitations only; never copy IFC data, report rows, output files, building details, or local paths.

- [ ] **Step 1: Run the pinned 20-case benchmark and inspect deliverables**

Run the CLI benchmark against the actual FreeCADCmd/IfcOpenShell runtime, verify all five fresh jobs and five replays, expected QTO totals, eight reject codes, and two tamper failures. Open one generated `takeoff.json`, CSV, and manifest from a synthetic IFC fixture; check row-level provenance, SI conversion, coverage status, exact three-file output, path redaction, and `model_calls=0`.

- [ ] **Step 2: Run the owning product's full verification**

Run `py -3.12 -m pytest -q`, `py -3.12 -m ruff check src tests`, `py -3.12 -m mypy --strict` for the repository's supported targets, and `py -3.12 scripts/check.py`. Report unrelated intermittent failures separately; do not claim success from focused checks if the normal check fails.

- [ ] **Step 3: Inspect only scoped nested changes and commit after gates pass**

On nested repository `main`, inspect the full diff and stage only BIM source, tests, example, docs, and lock resources. Commit as `feat(bim): add provenance-preserving IFC quantity takeoff V1A`. Push only if this product repo already has a configured upstream; do not create a remote or deploy. Keep root Solo Empire on its existing `main` and preserve unrelated root work.

- [ ] **Step 4: Record root evidence and run documentation checks**

Add the summary evaluation and roadmap status only after confirming the nested commit SHA and acceptance evidence. Run `task docs:catalog`, `task docs:catalog:check`, `task docs:links:check`, `task docs:numbers:check`, and `task docs:check`; report any known baseline failure without editing unrelated registry/database rows.

- [ ] **Step 5: Review root scope and final evidence**

Run `git diff --check` and `git status --short` in the root and nested repositories; ensure only approved BIM plan/spec/evaluation/roadmap/catalog files are included in root changes. Keep all generated IFCs, reports, temporary files, and benchmark output in ignored product `.runtime/bim/`.

**Completion gate:** The real pinned FreeCADCmd worker imports the existing IfcOpenShell files through a verified path shorter than 260 characters; valid synthetic IFC2X3 and IFC4 files produce expected occurrence-only QTO rows and SI aggregates; all invalid/unit/coverage/replay cases behave as specified; the 20-case benchmark passes with `model_calls=0`; product verification passes; and summarized root evidence points to a real nested commit. If runtime preflight or required schema/QTO/unit APIs fail, stop before claiming readiness and return the design for review.

## Plan Review State

The user approved the revised written design on 2026-09-25 and authorized
implementation by replying “please continue” to the plan-review gate on
2026-09-25. Execute the plan with the previously selected Native method on
the owning product repository's `main` branch.
