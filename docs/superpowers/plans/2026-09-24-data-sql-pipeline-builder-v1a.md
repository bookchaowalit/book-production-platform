# Data SQL/Pipeline Builder V1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Status:** Implementation committed locally on nested `main` as `bde147e`;
the 20-case synthetic acceptance gate passed. The scoped Solo Empire
documentation handoff and runtime-boundary registration are included in this
delivery. Keep the product plan-only and deterministic (`model_calls=0`).

**Goal:** Build a local, deterministic compiler that turns a typed pipeline graph into a validated, read-only SQLite query with parameters, lineage, and replay-safe artifacts.

**Architecture:** Add a Data Pipeline IR and local plan-only compiler as a new domain adapter in the existing `book-production-platform` repository. Give it a `book-production data pipeline` CLI and a loopback-only local API; validate SQL only against an in-memory schema, and never connect to or execute against customer data.

**Tech Stack:** Existing Python, Pydantic, FastAPI, pytest, Ruff, mypy, and standard-library `sqlite3`; add no dependency.

**Spec:** [Data SQL/Pipeline Builder V1A Design](../specs/2026-09-24-data-sql-pipeline-builder-design.md)

**Repository boundary:** All `src/`, `tests/`, `README.md`, and `scripts/` paths below are relative to `the `book-production-platform` repository`. Keep the Solo Empire checkout on `main` and change only the spec/plan, roadmap, catalog, and runtime-boundary registration there. Do not create another product repository or add implementation code to the control repository. Existing Data + Data Viz V1A stays in its current checkout and is not migrated by this slice.

## Global Constraints

- Standard path is structured IR → validation → read-only SQLite SQL → in-memory `EXPLAIN QUERY PLAN` → local artifacts.
- Input has one declared source table, 1–100 typed columns, 1–20 nodes, and one output node.
- The serialized request is limited to 262,144 UTF-8 bytes; column labels are 1–120 characters and optional source names are 1–128 characters.
- `select.columns`, `group_by`, and `metrics` are each capped at 100; string filter values are capped at 2,048 characters; non-finite decimals and non-scalar values are rejected.
- Requests use `pipeline-request.v1`; generated IR and positional typed parameters use `pipeline-ir.v1` and `pipeline-parameters.v1`.
- `pipeline_id`, `table_name`, `node_id`, and metric alias use the exact patterns in the linked spec; source refs are bounded opaque lineage strings.
- Filter literals must match the input column's declared type; no implicit coercion is allowed.
- Only `select`, `filter_equals`, `filter_null`, `sort_by`, `limit`, `aggregate`, and schema-compatible `union_all` are allowed.
- SQL values always use bound `?` parameters; identifiers match the schema pattern and are double-quoted.
- No arbitrary SQL, DDL, DML, connection string, credential, network access, source row read, query execution, publishing, or write action.
- Artifacts are private local files: `pipeline.sql`, typed `parameters.json`, `report.json`, and `qa-manifest.json`; a separate `.receipts/<fingerprint>.json` pins all four hashes. Values and source metadata never enter logs or telemetry; API response bodies contain metadata only.
- HTTP service use is bound to `127.0.0.1`; there is no remote or production deployment path in V1A.
- Synthetic acceptance is 20 cases with zero unexpected outcomes, zero SQL-plan failures, and zero model calls; local compute cost remains unknown.

## Review Focus

- A filter value shaped like SQL remains only a bound parameter; test that it is absent from generated SQL and present in `parameters.json`.
- A graph with a cycle, dangling parent, duplicate node id, or unreachable node is rejected before SQL is emitted; test each error class.
- An aggregation over text, or `union_all` over mismatched ordered schemas, is rejected with a stable validation code.
- A `limit` without terminal `sort_by` is rejected; tied sort values use every remaining schema column as stable tie-breakers before limiting.
- Unicode labels and schema keys are preserved in reports while only validated identifiers enter quoted SQL.
- A filter literal with a type different from its declared input column is rejected; parameter serialization preserves date and decimal types.
- Parameter values and source metadata stay in private local artifacts, never in logs, telemetry, API response content, or benchmark summaries.
- An unchanged request replays only after receipt and artifact hashes verify; changed requests produce new fingerprints, and corrupted prior output fails closed without overwrite.

---

### Task 1: Versioned Pipeline IR and graph validation

**Files:**

- Create `src/book_production_platform/data_pipeline_ir/models.py`
- Create `src/book_production_platform/data_pipeline_ir/validation.py`
- Create `src/book_production_platform/data_pipeline_ir/__init__.py`
- Create `tests/data_pipeline_ir/__init__.py` so pytest assigns a unique import package to this suite
- Test `tests/data_pipeline_ir/test_validation.py`

**Interfaces:**

- `build_pipeline_ir(request: PipelineRequest | dict[str, Any]) -> PipelineIR`
- `PipelineIR.ordered_nodes` is topologically ordered; `PipelineIR.output_columns` is the final typed schema; `PipelineIR.output_lineage` maps every output key to `PipelineLineageRef` records.
- `PipelineValidationError(ValueError)` carries a list of `ValidationIssue` items with stable error codes.
- `PipelineLineageRef` has `source_ref: str`, `table_name: str`, and `column_key: str | None`.
- `PipelineRequest` has `schema_version="pipeline-request.v1"`; `PipelineIR` carries `schema_version="pipeline-ir.v1"`.
- Operation-only fields default to absent, and the validator uses `model_fields_set` so explicitly supplied irrelevant fields are rejected even when null/default-valued.
- Internal helpers: `_topological_order(nodes: list[PipelineNode]) -> list[PipelineNode]`, `_propagate_schemas(source_columns: list[ColumnSpec], ordered_nodes: list[PipelineNode]) -> dict[str, list[ColumnSpec]]`, `_propagate_lineage(source: PipelineSource, ordered_nodes: list[PipelineNode]) -> dict[str, dict[str, list[PipelineLineageRef]]]`, `_validate_output_reachability(output_node: str, ordered_nodes: list[PipelineNode]) -> None`.

- [x] **Step 1: Write the failing contract and graph tests**

```python
import pytest

from book_production_platform.data_pipeline_ir.validation import PipelineValidationError, build_pipeline_ir


def test_graph_rejects_cycle_and_unknown_parent() -> None:
    source = {
        "table_name": "orders",
        "source_ref": "warehouse.orders",
        "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
    }
    cyclic = {
        "pipeline_id": "cycle-case",
        "source": source,
        "nodes": [
            {"node_id": "a", "parent_ids": ["b"], "op": "filter_null", "column": "region"},
            {"node_id": "b", "parent_ids": ["a"], "op": "filter_null", "column": "region"},
        ],
        "output_node": "b",
    }
    unknown = {
        "pipeline_id": "parent-case",
        "source": source,
        "nodes": [{"node_id": "a", "parent_ids": ["missing"], "op": "filter_null", "column": "region"}],
        "output_node": "a",
    }
    for request, expected_code in ((cyclic, "pipeline_cycle"), (unknown, "unknown_pipeline_parent")):
        with pytest.raises(PipelineValidationError) as error:
            build_pipeline_ir(request)
        assert expected_code in {issue.code for issue in error.value.issues}
```

- [x] **Step 2: Run the tests and confirm the contract fails**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline_ir/test_validation.py -q`

Expected: FAIL because the new pipeline models and validator are not defined.

- [x] **Step 3: Add models and deterministic semantic validation**

Define `PipelineSource`, `PipelineMetric`, `PipelineNode`, `PipelineRequest`, `PipelineLineageRef`, and `PipelineIR`. Validate request size, source schema duplicates and field lengths, node fields per operation, parent cardinality, unique ids, bounded column keys, reachability, cycles, strict filter value types, schema and lineage propagation, metric types, `union_all` schema equality, output-node existence, and terminal sort/limit rules. Sort ready nodes lexicographically during topological traversal. Map Pydantic shape errors to `invalid_pipeline_request`, invalid source schemas to `invalid_pipeline_source_schema`, and size failures to `pipeline_request_too_large`.

```python
from typing import Any

from book_production_platform.data_pipeline_ir.models import PipelineIR, PipelineRequest


def build_pipeline_ir(request: PipelineRequest | dict[str, Any]) -> PipelineIR:
    parsed = request if isinstance(request, PipelineRequest) else PipelineRequest.model_validate(request)
    ordered = _topological_order(parsed.nodes)
    schemas_by_node = _propagate_schemas(parsed.source.columns, ordered)
    lineage_by_node = _propagate_lineage(parsed.source, ordered)
    _validate_output_reachability(parsed.output_node, ordered)
    return PipelineIR(
        pipeline_id=parsed.pipeline_id,
        source=parsed.source,
        ordered_nodes=ordered,
        output_node=parsed.output_node,
        schemas_by_node=schemas_by_node,
        output_columns=schemas_by_node[parsed.output_node],
        lineage_by_node=lineage_by_node,
        output_lineage=lineage_by_node[parsed.output_node],
        source_refs=[parsed.source.source_ref],
    )
```

- [x] **Step 4: Test all declared operations and rejection cases**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline_ir/test_validation.py -q`

Expected: PASS for stable topological ordering, schema and lineage output, type-compatible filters, unique projections/aggregate outputs, and terminal sort-then-limit with deterministic tie-break columns; all failures use the stable codes declared in the design spec, including cycle, type, field, id, parent, reachability, union-schema, invalid source schema, oversized request, and nondeterministic-limit.

- [x] **Step 5: Commit the IR contract**

Stage only the Task 1 implementation and test files in `book-production-platform` and commit as `feat: add data pipeline IR`. Completed at `d33caf5` after the 53-case IR suite passed.

### Task 2: Parameterized SQLite query compiler

**Files:**

- Create `src/book_production_platform/data_pipeline/pipeline.py`
- Test `tests/data_pipeline/test_pipeline_compile.py`

**Interfaces:**

- `compile_pipeline_ir(pipeline: PipelineIR) -> CompiledPipeline`
- `CompiledPipeline` contains SQL, ordered scalar `parameters`, matching `parameter_types: list[ColumnType]`, output columns, output lineage, source refs, and `model_calls=0`.
- The compiler's in-memory parameter values retain Python scalar types; the serialized parameter artifact uses typed entries and fixed-point Decimal / ISO date strings.
- Test helper `pipeline_fixture(filter_value: CellValue) -> PipelineIR` builds a source table named `orders` with a text `region` column and one `filter_equals` node.
- Compiler helpers: `_quote_identifier(identifier: str) -> str`, `_compile_nodes(pipeline: PipelineIR) -> tuple[list[str], list[CellValue], list[ColumnType], dict[str, str]]`, and `parameter_document(compiled: CompiledPipeline) -> dict[str, Any]`.
- `ExplainStep` has `node_id: int`, `parent_id: int`, and `detail: str`; `ExplainResult` has `sqlite_version: str` and `steps: tuple[ExplainStep, ...]`.
- `explain_pipeline_sql(pipeline: PipelineIR, compiled: CompiledPipeline) -> ExplainResult` creates only an in-memory schema, runs `EXPLAIN QUERY PLAN`, and returns normalized plan rows plus the SQLite version; it never executes the SELECT.
- `PipelineExplainError(ValueError)` carries only the stable code `pipeline_sql_invalid`, never SQL or bound values.
- `CompiledPipeline` is defined in `src/book_production_platform/data_pipeline/pipeline.py`; only `parameter_document` normalizes `Decimal`/`date` for JSON serialization.

```python
from book_production_platform.data_pipeline_ir.models import CellValue, ColumnType, PipelineIR
from book_production_platform.data_pipeline_ir.validation import build_pipeline_ir


def pipeline_fixture(filter_value: CellValue) -> PipelineIR:
    return build_pipeline_ir(
        {
            "pipeline_id": "filter-case",
            "source": {
                "table_name": "orders",
                "source_ref": "warehouse.orders",
                "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
            },
            "nodes": [
                {
                    "node_id": "filtered",
                    "parent_ids": ["source"],
                    "op": "filter_equals",
                    "column": "region",
                    "value": filter_value,
                }
            ],
            "output_node": "filtered",
        }
    )
```

- [x] **Step 1: Write failing compiler safety and determinism tests**

```python
from book_production_platform.data_pipeline.pipeline import compile_pipeline_ir


def test_filter_value_is_bound_not_interpolated() -> None:
    compiled = compile_pipeline_ir(pipeline_fixture("BKK' OR 1=1 --"))
    assert "BKK' OR 1=1 --" not in compiled.sql
    assert compiled.sql.count("?") == 1
    assert compiled.parameters == ["BKK' OR 1=1 --"]
    assert compiled.sql == compile_pipeline_ir(pipeline_fixture("BKK' OR 1=1 --")).sql
```

- [x] **Step 2: Run the test and confirm compilation is missing**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_pipeline_compile.py -q`

Expected: FAIL because `compile_pipeline_ir` is not defined.

- [x] **Step 3: Compile each allowlisted node into stable CTE SQL**

Assign CTE names `step_0`, `step_1`, … from the validated topological order. Quote all schema identifiers. Compile filters and limits with ordered `?` parameters, sort direction from the enum, aggregates from the operation enum, and unions only after the IR validator proves matching schemas. For a `limit`, compile the requested sort key followed by all remaining parent-schema keys in ascending order in the limit CTE and final SELECT, so ties have deterministic selection and presentation. Other `sort_by` outputs use only their requested key.

```python
def compile_pipeline_ir(pipeline: PipelineIR) -> CompiledPipeline:
    ctes, parameters, parameter_types, cte_names = _compile_nodes(pipeline)
    sql = f"WITH {', '.join(ctes)} SELECT * FROM {cte_names[pipeline.output_node]}"
    return CompiledPipeline(
        pipeline_id=pipeline.pipeline_id,
        dialect="sqlite",
        sql=sql,
        parameters=parameters,
        parameter_types=parameter_types,
        output_node=pipeline.output_node,
        output_columns=pipeline.output_columns,
        output_lineage=pipeline.output_lineage,
        source_refs=pipeline.source_refs,
        model_calls=0,
    )
```

Serialize the separate `parameters.json` artifact using
`parameter_document(compiled)`, with the exact shape
`{"schema_version":"pipeline-parameters.v1","parameters":[{"type":"text","value":"..."}]}`.
Pair each scalar with its `parameter_types` entry, including `integer` for a
limit placeholder. Emit `Decimal` as a fixed-point string, `date` as ISO-8601,
`boolean` as a JSON boolean, and keep text/integer as JSON scalar values.

- [x] **Step 4: Validate the plan with SQLite `EXPLAIN QUERY PLAN`**

Create a temporary in-memory table from validated schema types (`text`→`TEXT`, `integer`→`INTEGER`, `decimal`→`NUMERIC`, `date`→`TEXT`, `boolean`→`INTEGER`), run `EXPLAIN QUERY PLAN` with bound parameters, normalize its rows and record `sqlite_version()`, then close the connection. Test supported operations, non-empty explain rows, Unicode schema labels, missing columns, terminal sort/limit tie-breakers, and filter values that resemble SQL.

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_pipeline_compile.py -q`

Expected: PASS; no SQL execution occurs and no user value is embedded in SQL.

- [x] **Step 5: Commit the compiler**

Stage only the compiler and its test in `book-production-platform`; commit as `feat: compile safe data pipeline SQL`. Completed at `7f9c803`; the targeted nine-case suite and 265-case package suite passed, with two host-dependent symlink checks skipped.

### Task 3: Replay-safe plan artifacts and content QA

**Files:**

- Create `src/book_production_platform/data_pipeline/jobs.py`
- Create `src/book_production_platform/data_pipeline/qa.py`
- Create `tests/data_pipeline/__init__.py` to avoid pytest's bare-module collision with spreadsheet tests
- Modify `tests/data_pipeline/conftest.py`
- Test `tests/data_pipeline/test_jobs.py`

**Interfaces:**

- `run_pipeline_job(request: PipelineRequest | dict[str, Any], output_root: str | Path) -> PipelineJobResult`
- `qa_pipeline_artifacts(pipeline: PipelineIR, compiled: CompiledPipeline, query_plan: ExplainResult, staging_dir: Path) -> tuple[str, ...]`
- `build_pipeline_report(pipeline: PipelineIR, compiled: CompiledPipeline, query_plan: tuple[ExplainStep, ...], sqlite_version: str) -> dict[str, Any]`
- `PipelineJobResult` fields are `fingerprint`, `status`, `artifact_dir`, `artifact_hashes`, `qa`, `model_calls`, `replay`, `bytes_written`, `error_code`, and `metadata` with `execution_mode=plan_only` and `query_plan_verified`; `to_dict()` omits the request, source refs, and parameter values.
- `PipelineJobError(ValueError)` sets `.code` to one stable code and uses that code as its message; `_verify_replay` raises `PipelineJobError("replay_integrity_error")` for missing, malformed, symlinked, or hash-mismatched existing output.
- Canonical request plus `RECIPE_VERSION="data-sql-pipeline-builder-v1a"` determines the 64-character fingerprint and `jobs/<fingerprint>` directory. `.receipts/<fingerprint>.json` pins the hashes of `pipeline.sql`, `parameters.json`, `report.json`, and `qa-manifest.json`.
- Helpers: `_parse_pipeline_request(request: PipelineRequest | dict[str, Any]) -> PipelineRequest`, `_canonical_fingerprint(request: PipelineRequest, recipe_version: str) -> str`, `_idempotency_lock(path: Path) -> ContextManager[None]`, `_create_staging_dir(root: Path, fingerprint: str) -> Path`, `_write_staged_artifacts(staging_dir: Path, files: dict[str, bytes]) -> None`, `_remove_staging_dir(staging_dir: Path) -> None`, `_hash_artifacts(staging_dir: Path, files: dict[str, bytes]) -> dict[str, str]`, `_hash_all_artifacts(staging_dir: Path) -> dict[str, str]`, `_build_qa_manifest(request: PipelineRequest, fingerprint: str, hashes: dict[str, str], qa: tuple[str, ...], query_plan: ExplainResult) -> dict[str, Any]`, `_build_receipt(fingerprint: str, hashes: dict[str, str]) -> dict[str, Any]`, `_publish_job(staging_dir: Path, artifact_dir: Path, receipt_path: Path, receipt: dict[str, Any]) -> None`, `_verify_replay(fingerprint: str, artifact_dir: Path, receipt_path: Path) -> PipelineJobResult`, `_job_bytes(artifact_dir: Path, receipt_path: Path) -> int`, `_accepted_result(fingerprint: str, artifact_dir: Path, hashes: dict[str, str], qa: tuple[str, ...], bytes_written: int) -> PipelineJobResult`, and `_rejected_result(error_code: str, qa: tuple[str, ...] = ()) -> PipelineJobResult`.
- Test fixture `valid_pipeline_request() -> dict[str, Any]` is defined in `tests/data_pipeline/conftest.py` and returns a filter-then-select graph over source column `region`.
- Adapt the content-addressed receipt, fingerprint lock, symlink rejection, and staged-directory publication pattern from `src/book_production_platform/scene3d/jobs.py`; keep Data Pipeline's receipt schema and artifact set domain-specific rather than introducing another shared persistence abstraction.

```python
from typing import Any

import pytest


@pytest.fixture
def valid_pipeline_request() -> dict[str, Any]:
    return {
        "pipeline_id": "regions-only",
        "source": {
            "table_name": "orders",
            "source_ref": "warehouse.orders",
            "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
        },
        "nodes": [
            {"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": "BKK"},
            {"node_id": "selected", "parent_ids": ["filtered"], "op": "select", "columns": ["region"]},
        ],
        "output_node": "selected",
    }
```

- [x] **Step 1: Test accepted artifacts, no parameter leakage, replay, tamper, and content fingerprints**

```python
import json
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline.jobs import PipelineJobError, run_pipeline_job


def test_pipeline_job_replays_without_rewriting(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    request = valid_pipeline_request
    first = run_pipeline_job(request, tmp_path)
    second = run_pipeline_job(request, tmp_path)
    assert first.status == second.status == "accepted"
    assert first.fingerprint == second.fingerprint
    assert second.replay is True and second.bytes_written == 0
    assert set(first.artifact_hashes) == {"pipeline.sql", "parameters.json", "report.json", "qa-manifest.json"}
    assert first.artifact_dir is not None
    report = first.artifact_dir / "report.json"
    manifest = first.artifact_dir / "qa-manifest.json"
    receipt = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    parameters = first.artifact_dir / "parameters.json"
    report_text = report.read_text(encoding="utf-8")
    manifest_text = manifest.read_text(encoding="utf-8")
    report_payload = json.loads(report_text)
    assert "BKK" not in report_text
    assert report_payload["query_plan"]
    assert report_payload["sqlite_version"]
    assert first.metadata["query_plan_verified"] is True
    assert "BKK" not in manifest_text
    assert "warehouse.orders" not in manifest_text
    assert "BKK" not in receipt.read_text(encoding="utf-8")
    assert json.loads(parameters.read_text(encoding="utf-8")) == {
        "schema_version": "pipeline-parameters.v1",
        "parameters": [{"type": "text", "value": "BKK"}],
    }


def test_changed_request_gets_a_distinct_fingerprint_and_job(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    request = json.loads(json.dumps(valid_pipeline_request))
    changed = json.loads(json.dumps(valid_pipeline_request))
    changed["nodes"][0]["value"] = "CNX"
    first = run_pipeline_job(request, tmp_path)
    second = run_pipeline_job(changed, tmp_path)
    assert first.fingerprint != second.fingerprint
    assert first.artifact_dir != second.artifact_dir
    assert first.artifact_dir is not None and second.artifact_dir is not None
    assert first.artifact_dir.exists() and second.artifact_dir.exists()


def test_tampered_artifact_fails_closed(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    assert first.artifact_dir is not None
    (first.artifact_dir / "parameters.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)


def test_tampered_receipt_fails_closed(tmp_path: Path, valid_pipeline_request: dict[str, Any]) -> None:
    first = run_pipeline_job(valid_pipeline_request, tmp_path)
    receipt = tmp_path / ".receipts" / f"{first.fingerprint}.json"
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(PipelineJobError, match="replay_integrity_error"):
        run_pipeline_job(valid_pipeline_request, tmp_path)
```

- [x] **Step 2: Run the tests and confirm the job runner is missing**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_jobs.py -q`

Expected: FAIL because the data-pipeline job module does not yet exist.

- [x] **Step 3: Write atomic artifacts, QA manifest, receipt, and content QA**

Hash every artifact; validate the SQL remains one read-only statement; verify typed parameters match placeholder order; ensure the report carries output schema, per-column lineage, operation order, source reference, SQL hash, non-empty normalized explain rows, and SQLite version. The QA manifest records schema/recipe versions, fingerprint, hashes for the first three artifacts, QA, `execution_mode=plan_only`, `model_calls=0`, and `query_plan_verified=true`, while excluding the request, source reference, and parameter values. The separate receipt pins hashes for all four files including the QA manifest. QA reruns `EXPLAIN QUERY PLAN` and compares its normalized rows with the report. `PipelineJobResult.to_dict()` returns status, fingerprint, artifact names/hashes, model-call/replay counts, stable codes, and artifact path only. Treat every artifact as private local output.

```python
from pathlib import Path
from typing import Any

from book_production_platform.data_pipeline.pipeline import (
    PipelineExplainError,
    build_pipeline_report,
    compile_pipeline_ir,
    explain_pipeline_sql,
    parameter_document,
)
from book_production_platform.data_pipeline_ir.validation import (
    PipelineValidationError,
    build_pipeline_ir,
)


def run_pipeline_job(request: PipelineRequest | dict[str, Any], output_root: str | Path) -> PipelineJobResult:
    try:
        parsed = _parse_pipeline_request(request)
        pipeline = build_pipeline_ir(parsed)
        fingerprint = _canonical_fingerprint(parsed, "data-sql-pipeline-builder-v1a")
    except PipelineValidationError as exc:
        codes = tuple(issue.code for issue in exc.issues)
        return _rejected_result(codes[0] if codes else "invalid_pipeline_request", codes)
    root = Path(output_root)
    artifact_dir = root / "jobs" / fingerprint
    receipt_path = root / ".receipts" / f"{fingerprint}.json"
    with _idempotency_lock(root / ".locks" / f"{fingerprint}.lock"):
        if artifact_dir.exists() or receipt_path.exists():
            return _verify_replay(fingerprint, artifact_dir, receipt_path)
        compiled = compile_pipeline_ir(pipeline)
        try:
            query_plan = explain_pipeline_sql(pipeline, compiled)
        except PipelineExplainError:
            return _rejected_result("pipeline_sql_invalid")
        parameter_bytes = _json_bytes(parameter_document(compiled))
        report_bytes = _json_bytes(build_pipeline_report(pipeline, compiled, query_plan.steps, query_plan.sqlite_version))
        artifact_bytes = {"pipeline.sql": compiled.sql.encode("utf-8"), "parameters.json": parameter_bytes, "report.json": report_bytes}
        staging_dir = _create_staging_dir(root, fingerprint)
        try:
            _write_staged_artifacts(staging_dir, artifact_bytes)
            qa = qa_pipeline_artifacts(pipeline, compiled, query_plan, staging_dir)
            if qa:
                return _rejected_result("pipeline_artifact_qa_failed", qa)
            artifact_hashes = _hash_artifacts(staging_dir, artifact_bytes)
            manifest = _build_qa_manifest(parsed, fingerprint, artifact_hashes, qa, query_plan)
            _write_staged_artifacts(staging_dir, {"qa-manifest.json": _json_bytes(manifest)})
            all_hashes = _hash_all_artifacts(staging_dir)
            receipt = _build_receipt(fingerprint, all_hashes)
            _publish_job(staging_dir, artifact_dir, receipt_path, receipt)
        finally:
            if staging_dir.exists():
                _remove_staging_dir(staging_dir)
        return _accepted_result(fingerprint, artifact_dir, all_hashes, qa, _job_bytes(artifact_dir, receipt_path))
```

`_parse_pipeline_request` checks the 262,144-byte canonical request bound and
converts Pydantic shape failures to `invalid_pipeline_request`. `_create_staging_dir`,
`_write_staged_artifacts`, `_remove_staging_dir`, `_hash_artifacts`,
`_hash_all_artifacts`, `_build_receipt`, and `_accepted_result` have concrete
interfaces defined in this task. `_publish_job` uses the platform's atomic
directory publication pattern, refuses symlinked/occupied destinations, and
writes the separate receipt only after the staged files pass QA. Any orphaned
artifact directory or receipt fails closed with `replay_integrity_error`; it
is never overwritten.

- [x] **Step 4: Verify replay and tamper behavior**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_jobs.py -q`

Expected: PASS for same-input replay with zero new bytes, a changed request producing a different fingerprint and output folder, tampered artifact/receipt rejection as `replay_integrity_error`, and query-plan validation. Add a symlink-rejection test that skips only when the host cannot create symlinks.

- [x] **Step 5: Commit plan jobs and QA**

Stage only the Task 3 source and test files in `book-production-platform`; commit as `feat: add content-addressed pipeline plan jobs`. Completed at `2091902`; nine task tests passed, two symlink cases skipped because Windows does not grant symlink creation, and the complete product suite passed 274 tests with four host-dependent symlink skips. A later full-suite run surfaced a transient Windows staging rename `PermissionError`; follow-up `51c5da8` adds bounded retries and a regression test, after which the complete suite passed 286 tests.

### Task 4: Local API and CLI

**Files:**

- Create `src/book_production_platform/data_pipeline/main.py`
- Create `src/book_production_platform/data_pipeline/cli.py`
- Modify `src/book_production_platform/cli.py` to dispatch `book-production data ...`
- Test `tests/data_pipeline/test_api.py`
- Test `tests/data_pipeline/test_cli.py`

**Interfaces:**

- `POST /v1/data/pipelines` accepts `{ "pipeline": { ...PipelineRequest... } }`; request fingerprinting supplies replay identity.
- `book-production data pipeline build --input request.json --output-root DIR` writes only local plan artifacts.
- `PipelinePayload` is a `BaseModel` with `extra="forbid"` and a typed `PipelineRequest` field named `pipeline`.
- HTTP server instructions bind only to `127.0.0.1`; the API response exposes artifact metadata and local paths, never artifact contents, SQL parameters, or source metadata.

- [x] **Step 1: Add failing API and CLI tests**

```python
from fastapi.testclient import TestClient

from book_production_platform.data_pipeline.main import app


def test_pipeline_api_returns_a_plan_only_result(tmp_path, monkeypatch, valid_pipeline_request) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))
    response = TestClient(app).post(
        "/v1/data/pipelines",
        json={"pipeline": valid_pipeline_request},
    )
    assert response.status_code == 200
    assert response.json()["model_calls"] == 0
    assert response.json()["metadata"]["execution_mode"] == "plan_only"
    assert "BKK" not in response.text
    assert "warehouse.orders" not in response.text
```

Also test validation/oversize errors, same-input replay, changed-input new fingerprint, tampered-artifact rejection, and that CLI output lists only local filenames and hashes and contains neither `BKK` nor `warehouse.orders`.

```python
import json
from pathlib import Path
from typing import Any

from book_production_platform.cli import main


def test_pipeline_cli_writes_a_local_plan(tmp_path: Path, capsys, valid_pipeline_request: dict[str, Any]) -> None:
    source = tmp_path / "pipeline.json"
    source.write_text(json.dumps(valid_pipeline_request), encoding="utf-8")
    code = main(["data", "pipeline", "build", "--input", str(source), "--output-root", str(tmp_path / "out")])
    output = capsys.readouterr().out
    result = json.loads(output)
    assert code == 0
    assert result["status"] == "accepted"
    assert result["model_calls"] == 0
    assert "BKK" not in output
    assert "warehouse.orders" not in output
```

- [x] **Step 2: Run the tests and confirm the route/command are absent**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_api.py tests/data_pipeline/test_cli.py -q`

Expected: FAIL with missing route/CLI behavior.

- [x] **Step 3: Add the plan-only API route and CLI subcommand**

Use `DATA_PIPELINE_OUTPUT_ROOT` and error mapping (`409` for `replay_integrity_error`, `413` for oversized request, `422` for invalid request). Parse the raw request body with `PipelinePayload.model_validate_json` inside the handler and map Pydantic parse failures to a stable `invalid_pipeline_request` response without echoing validation input. New requests and identical replays both return status 200 with the content fingerprint and a `replay` flag. Do not add a database URL, credential field, execution flag, publishing route, or user-supplied idempotency key. Document serving the local API bound only to `127.0.0.1`; do not document a remotely bound or hosted configuration.

```python
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from book_production_platform.data_pipeline.jobs import PipelineJobError, run_pipeline_job
from book_production_platform.data_pipeline_ir.models import PipelineRequest

app = FastAPI(title="Data SQL/Pipeline Builder V1A")


def _output_root() -> Path:
    return Path(os.environ.get("DATA_PIPELINE_OUTPUT_ROOT", "data-pipeline-output"))


class PipelinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipeline: PipelineRequest


@app.post("/v1/data/pipelines", status_code=200)
async def create_pipeline(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > 262_144:
        raise HTTPException(status_code=413, detail={"code": "pipeline_request_too_large"})
    try:
        payload = PipelinePayload.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_pipeline_request"}) from exc
    try:
        result = run_pipeline_job(payload.pipeline, _output_root())
    except PipelineJobError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    if result.status == "rejected":
        raise HTTPException(status_code=422, detail=result.to_dict())
    return result.to_dict()
```

Add the `data pipeline build` CLI path with a required input and output root. Read the input bytes, reject files over 262,144 bytes with `pipeline_request_too_large`, then use `PipelineRequest.model_validate_json` to preserve exact Decimal parsing; map parse errors to `invalid_pipeline_request`. Its JSON summary contains status, fingerprint, model-call/replay counts, artifact names and hashes only; it never prints artifact contents:

```python
# In src/book_production_platform/cli.py, add alongside the family imports:
from book_production_platform.data_pipeline.cli import main as data_main


# Inside _parser():
data = groups.add_parser("data", help="Data Pipeline Builder V1A operations")
data.add_argument("data_args", nargs=argparse.REMAINDER)
```

```python
# In src/book_production_platform/cli.py, inside main():
if args.family == "data":
    return data_main(args.data_args)
```

```python
# In src/book_production_platform/data_pipeline/cli.py:
import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from book_production_platform.data_pipeline.jobs import run_pipeline_job
from book_production_platform.data_pipeline_ir.models import PipelineRequest


class PipelineInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="book-production data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pipeline = subparsers.add_parser("pipeline")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    pipeline_build = pipeline_commands.add_parser("build")
    pipeline_build.add_argument("--input", required=True, type=Path)
    pipeline_build.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    body = args.input.read_bytes()
    if len(body) > 262_144:
        raise PipelineInputError("pipeline_request_too_large")
    request = PipelineRequest.model_validate_json(body)
    result = run_pipeline_job(request, args.output_root)
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "accepted" else 2
```

Handle Pydantic errors, `PipelineInputError`, and `OSError` in the new CLI with stable codes (`invalid_pipeline_request`, the input error's `.code`, and `cli_io_error`) and never echo raw input or exception text. In top-level `book_production_platform.cli.main`, dispatch `family == "data"` to `data_main(args.data_args)`; preserve every existing family command.

- [x] **Step 4: Verify API and CLI behavior**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_api.py tests/data_pipeline/test_cli.py -q`.

Expected: PASS and no external network or database access.

- [x] **Step 5: Commit API and CLI**

Stage only the Data Pipeline API/CLI and tests in `book-production-platform`; commit as `feat: expose local data pipeline builder`.

### Task 5: Synthetic benchmark and handoff evidence

**Files:**

- Create `src/book_production_platform/data_pipeline/scripts/pipeline_benchmark.py`
- Create `tests/data_pipeline/fixtures/pipelines_20.json`
- Test `tests/data_pipeline/test_benchmark.py`
- Modify `scripts/check.py`
- Modify `README.md`
- Modify `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`
- Modify `repository-catalog/registries/runtime-boundaries.yaml` to register the adapter under `book-production-platform`.

- [x] **Step 1: Add a 20-case fixture and failing benchmark assertions**

The fixture has 12 valid pipelines and 8 expected rejections. Use one shared source schema (`region:text`, `amount:decimal`, `created:date`, `active:boolean`, `units:integer`) and these cases:

| Cases | Expected | Coverage |
|---|---|---|
| `select`, `filter-text`, `filter-null`, `sort`, `sort-limit`, `count-rows`, `count-column`, `sum-decimal`, `avg-decimal`, `union-compatible`, `sql-shaped-filter`, `typed-date-decimal-filters` | accepted | Every operation, typed filters, safe parameter binding, deterministic limit tie-breaks, and union lineage |
| `cycle`, `unknown-parent`, `dead-node`, `unknown-column`, `filter-type-mismatch`, `sum-text`, `union-schema-mismatch`, `limit-without-sort` | rejected | Graph, schema, type, union, and deterministic-limit validation |

Every case declares its id, request, and expected status. SQL-shaped filter text is an accepted input; tests assert it appears only in `parameters.json`, never in SQL, report, manifest, CLI/API response, or benchmark summary.

- [x] **Step 2: Run the benchmark test and confirm the report is absent**

Run from the `book-production-platform` repository root: `python -m pytest tests/data_pipeline/test_benchmark.py -q`

Expected: FAIL because the pipeline benchmark is not defined.

```python
import json
from pathlib import Path

from book_production_platform.data_pipeline.jobs import run_pipeline_job
from book_production_platform.data_pipeline.scripts.pipeline_benchmark import run_pipeline_benchmark

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pipelines_20.json"


def test_pipeline_benchmark_matches_all_expected_outcomes(tmp_path: Path) -> None:
    summary = run_pipeline_benchmark(FIXTURE_PATH, tmp_path)
    assert summary["total"] == 20
    assert summary["accepted"] == 12
    assert summary["rejected"] == 8
    assert summary["unexpected"] == 0
    assert summary["query_plan_failures"] == 0
    assert summary["lineage_failures"] == 0
    assert summary["replay_hits"] == 12
    assert summary["model_calls"] == 0
    assert summary["bytes_written"] > 0
```

- [x] **Step 3: Report accepted/rejected counts, query-plan failures, lineage failures, replay hits, model calls, and bytes written**

Refuse a non-empty output root. Run each case through content-addressed `run_pipeline_job`, reopen artifacts, check each expected status, and replay all accepted cases with the same request. Count an explain failure when an accepted result lacks `metadata.query_plan_verified=true` or its reopened report has no explain rows. Do not include raw parameter values or source references in benchmark summaries.

```python
def run_pipeline_benchmark(fixture_path: Path, output_root: Path) -> dict[str, int]:
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError("benchmark_output_directory_not_empty")
    accepted = rejected = unexpected = query_plan_failures = lineage_failures = replay_hits = model_calls = bytes_written = 0
    for case in cases:
        result = run_pipeline_job(case["request"], output_root)
        accepted += result.status == "accepted"
        rejected += result.status == "rejected"
        unexpected += result.status != case["expected_status"]
        model_calls += result.model_calls
        bytes_written += result.bytes_written
        if result.status != "accepted" or result.artifact_dir is None:
            continue
        report = json.loads((result.artifact_dir / "report.json").read_text(encoding="utf-8"))
        query_plan_failures += not bool(report.get("query_plan")) or result.metadata.get("query_plan_verified") is not True
        lineage = report.get("output_lineage", {})
        lineage_failures += not bool(lineage) or any(not refs for refs in lineage.values())
        replay = run_pipeline_job(case["request"], output_root)
        replay_hits += replay.replay and replay.bytes_written == 0
        unexpected += replay.status != "accepted" or not replay.replay
        model_calls += replay.model_calls
    return {"total": len(cases), "accepted": accepted, "rejected": rejected, "unexpected": unexpected,
            "query_plan_failures": query_plan_failures, "lineage_failures": lineage_failures,
            "replay_hits": replay_hits, "model_calls": model_calls, "bytes_written": bytes_written}
```

- [x] **Step 4: Run full product verification**

Run from the `book-production-platform` repository root:

```text
python -m pytest tests/data_pipeline_ir tests/data_pipeline -q
ruff check src/book_production_platform/data_pipeline_ir src/book_production_platform/data_pipeline tests/data_pipeline_ir tests/data_pipeline
mypy --strict src/book_production_platform/data_pipeline_ir src/book_production_platform/data_pipeline
python scripts/check.py
```

Expected: all tests and static checks pass; 20 benchmark cases have zero unexpected outcomes, zero query-plan/lineage failures, and zero model calls.

- [x] **Step 5: Update README and roadmap with measured evidence**

Record the measured benchmark result, verified no-execution boundary, private-artifact handling, loopback-only HTTP instructions, central-repository ownership, and `deployment_skipped_no_target`. Keep local compute cost `unknown` unless an approved rate card exists.

Measured locally on 2026-09-25: 20 cases, 12 accepted, 8 expected rejections, 0 unexpected outcomes, 0 query-plan failures, 0 lineage failures, 12 replay hits, 0 model calls, and 32,566 artifact bytes written. The SQL-shaped fixture value appears only in `parameters.json`; artifacts stay under the caller-managed local output root. The API launch example uses `127.0.0.1`, the adapter is registered to the shared `book-production-platform` repository, and local compute cost is `unknown`.

Full product verification passed: 291 tests, 4 skipped because Windows denied symlink creation, Ruff passed, and strict mypy passed. The suite emitted two FastAPI/Starlette test-client dependency deprecation warnings.

- [x] **Step 6: Commit and deliver**

In `book-production-platform`, stage only the Task 5 implementation/doc/check files and commit on its existing `main`. In Solo Empire `main`, stage only the roadmap, runtime-boundary registry, and catalog projection for this plan. Verify each repository's upstream before pushing; `book-production-platform` currently has no configured remote, so report that push is unavailable unless an upstream is configured later. There is no deployment target; report `deployment_skipped_no_target`.
