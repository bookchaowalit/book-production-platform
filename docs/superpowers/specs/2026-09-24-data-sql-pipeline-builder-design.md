# Data SQL/Pipeline Builder V1A — Design

**Status:** Implemented locally on 2026-09-25; the 20-case synthetic acceptance
gate passed in `book-production-platform` commit `bde147e`. V1A remains
plan-only and does not connect to or execute against a source database.

**Owner:** add this as a domain adapter in the existing `book-production-platform`
repository. Keep `solo-empire` as the plan, roadmap, catalog, and runtime-boundary
registry; do not create another repository or add the product runtime to the
control-plane checkout. Existing Data + Data Viz V1A code remains outside this
slice and is not migrated.

## Goal

Turn an explicitly structured, typed pipeline into deterministic, read-only
SQLite SQL with bound parameters, a visible query plan, source lineage, and
replayable local artifacts. The product builds and validates a query; it does
not connect to or execute against a customer database.

## User and outcome

A data analyst can describe a small data transformation as a typed graph,
inspect the SQL and parameters, and hand that plan to a separately authorized
runtime. They do not need to write SQL or trust generated SQL text.

## V1A input and intermediate representation

The package owns a data-pipeline-specific Pydantic contract that mirrors the
existing Data V1 scalar types without importing from the Solo Empire control
repository or Spreadsheet IR. `PipelineRequest` has
`schema_version="pipeline-request.v1"` (defaulted for
that version), a pipeline id, a declared source table and typed `ColumnSpec`
list, 1–20 operation nodes, and one output node. Models reject unknown fields.
The source table name uses a
bounded ASCII identifier; column keys retain the existing Data IR Unicode
identifier rule but are capped at 63 characters. The compiler always quotes
identifiers. The source reference is lineage metadata only; V1A does not
resolve paths, credentials, URLs, or database connections. `PipelineIR` uses
`schema_version="pipeline-ir.v1"`.

The serialized job request is capped at 262,144 UTF-8 bytes; larger HTTP or CLI
inputs fail with `pipeline_request_too_large` before producing files. Column
labels are 1–120 characters and optional source names are 1–128 characters.
This bounds parse, validation, compilation, and local artifact work; no source
rows are accepted.

`pipeline_id` uses `^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$`. `table_name` and metric
`alias` use `^[A-Za-z_][A-Za-z0-9_]{0,62}$`; `node_id` uses
`^[A-Za-z][A-Za-z0-9_-]{0,49}$`. `source_ref` is 1–255 characters and is
opaque metadata. Source columns contain 1–100 unique `ColumnSpec` keys and
unique source names.
`PipelineSource` fields are `table_name`, `source_ref`, and `columns`.
`PipelineNode` fields are `node_id`, `parent_ids`, `op`, `columns`, `column`,
`value`, `direction`, `group_by`, `metrics`, and `limit`. Operation-only
fields default to absent; validation checks explicitly supplied fields so an
irrelevant field is rejected even when its value is null or a default enum.
A metric has `alias`,
`op` (`count`, `sum`, or `avg`), and an optional `column`; `count` may count all
rows when `column` is omitted. Operation-specific fields are required as
described below and irrelevant fields are rejected.

The node id `source` is reserved and cannot be assigned to an operation node.

`nodes`, `select.columns`, `group_by`, and `metrics` are each bounded to 20,
100, 100, and 100 items respectively; a string filter value is bounded to
2,048 characters. The contract rejects non-finite decimals and non-scalar
filter values. A filter value must match its input column type:
`text`→string, `integer`→integer excluding booleans, `decimal`→integer or
finite `Decimal`, `date`→ISO date, and `boolean`→boolean. Use `filter_null` for
null checks. No implicit text/number/date coercion is performed.

The reserved root parent id is `source`. Each node uses `parent_ids`: every
operation except `union_all` requires exactly one known parent, while
`union_all` requires exactly two. Every declared node must be an ancestor of
the selected output; unused/dead nodes are rejected.

Each node has a stable id, parent node ids, and exactly one allowlisted
operation:

- `select`: retain a non-empty ordered list of known columns.
- `filter_equals`: compare a known column with one typed value using a bound
  parameter.
- `filter_null`: retain rows whose known column is null.
- `sort_by`: order by one known column and `asc` or `desc`.
- `limit`: keep between 1 and 10,000 rows.
- `aggregate`: group by known columns and emit `count`, `sum`, or `avg`
  metrics; `sum` and `avg` accept `integer` or `decimal` input columns only.
- `union_all`: combine two parent nodes only when their ordered schemas match.

`select` requires a non-empty list of unique `columns`; `filter_equals` requires
`column` and a non-null type-compatible `value`; `filter_null` and `sort_by`
require `column`; `limit`
requires an integer from 1 to 10,000 (excluding booleans); `aggregate` requires
at least one metric and accepts an empty `group_by` for a single total row.
`group_by` keys and metric aliases must be unique within their output schema.
`sum` and `avg` metrics require a
numeric `column`; `count` accepts a known input column or no column.
`count(column)` compiles to `COUNT(column)` and counts non-null values; `count`
without a column compiles to `COUNT(*)` and counts rows. Irrelevant operation
fields are rejected.

To keep row-limiting deterministic, `limit` is permitted only as the selected
output node and its direct parent must be `sort_by`. A `sort_by` node is
permitted only as the selected output or as the direct parent of that terminal
`limit`. When a limit follows a sort, the compiler uses the requested sort key
first, then appends every remaining column in that node's schema by key in
ascending order as deterministic tie-breakers. Rows identical across every
output column are interchangeable in the output multiset. The compiler puts
that order on the final query and on the same SQL node as `LIMIT`; it never
relies on row order inherited from an intermediate CTE.

For `union_all`, matching means equal ordered column keys and data types;
labels may differ, and the output keeps the first parent's labels. The union
combines lineage from both parents. Projection and pass-through nodes preserve
the source-column lineage. Aggregation group columns preserve their lineage;
each metric alias points to its input column, or to a table-level reference
when it counts all rows. Union lineage is the stable de-duplicated union of
both parents' lineage for each output column. A `PipelineLineageRef` has
`source_ref`, `table_name`, and optional `column_key`.
`PipelineIR.lineage_by_node` is keyed by node id then output column key;
`output_lineage` is keyed by output column key, and both map to lists of
`PipelineLineageRef`.

`build_pipeline_ir` rejects duplicate ids, unknown parents/columns, cycles,
dead nodes, incompatible `union_all` schemas, invalid operation fields, and
type mismatches. It returns a stable topological order and the output schema.
Node and CTE names generated for SQL come from that order, never from raw node
ids.

Stable validation codes are `invalid_pipeline_request`,
`pipeline_request_too_large`, `invalid_pipeline_source_schema`,
`duplicate_pipeline_node_id`, `duplicate_pipeline_parent`,
`reserved_pipeline_node_id`, `unknown_pipeline_parent`, `pipeline_cycle`,
`unknown_output_node`, `pipeline_dead_node`, `unknown_pipeline_column`,
`invalid_pipeline_operation_fields`, `pipeline_type_mismatch`,
`union_schema_mismatch`, and `nondeterministic_limit`. A failed in-memory SQL
explain returns `pipeline_sql_invalid` without echoing SQL or parameter values.

`PipelineIR` holds the validated source, ordered nodes, output node, per-node
schemas, output schema, per-node lineage, output lineage, and source
references. `CompiledPipeline` holds the pipeline id, SQLite dialect, SQL
text, ordered bound parameters and their `ColumnType` tags, output schema,
output lineage, source references, and `model_calls=0`. Aggregation output
schema uses the input type for `sum`, `decimal` for `avg`, and `integer` for
`count`; metric aliases must not duplicate group keys or each other. The fixed
recipe version is `data-sql-pipeline-builder-v1a`; any semantic or artifact
change requires a recipe version bump before release.

## Execution contract

The compiler targets SQLite SELECT syntax for V1A. It emits a single CTE-based
read query and a separately ordered parameter list. User-supplied values never
become SQL fragments. There is no arbitrary SQL, expression, join, DDL, DML,
dynamic extension, or query execution capability. The emitted query references
the declared logical `table_name`; a later, separately authorized consumer is
responsible for mapping that name to a real data source.

Parameter JSON uses typed entries (`{"type":"text","value":"..."}`), with
the type tag taken from the filter column or the `integer` limit operation;
decimals use fixed-point strings, dates use ISO-8601 strings, and booleans use
JSON booleans. The in-memory SQLite explain check binds decimals as fixed-point
strings, dates as ISO-8601 strings, and booleans as integers; other scalar
values retain their type.

Before accepting a job, QA creates a temporary in-memory SQLite schema from the
validated source columns and runs `EXPLAIN QUERY PLAN` with the bound values.
This validates the generated query without connecting to a database or reading
source rows. The in-memory schema is discarded before the job returns.

## Artifacts and cost policy

A successful local job writes `pipeline.sql`, `parameters.json`,
`report.json` (`schema_version="pipeline-report.v1"`), and
`qa-manifest.json` (`schema_version="pipeline-qa-manifest.v1"`); a separate
`data-pipeline.receipt.v1` receipt pins every artifact hash, including the QA
manifest. The report includes the source reference,
ordered operations, output schema, SQL hash, normalized `EXPLAIN QUERY PLAN`
rows, SQLite version, `execution_mode=plan_only`, and `model_calls=0`; it
omits parameter values. The parameter artifact uses
`{"schema_version":"pipeline-parameters.v1","parameters":[...]}` and
contains typed parameter entries in placeholder order. Because those values
and source metadata may be sensitive, all four artifacts are private local
output: they are never logged, sent to telemetry, uploaded, or included in API
response bodies. The response may expose status, stable QA/error codes,
artifact names and hashes, model-call/replay counts, the fingerprint, and local
artifact path only; it contains no request, source reference, or parameter
value. The caller chooses and manages output-directory access and retention;
no automatic deletion is performed.

The `qa-manifest.json` stores schema/recipe versions, the content fingerprint,
the hashes of SQL/parameters/report, explain rows, QA, model calls, and
non-sensitive runtime metadata; it does not copy the request, source reference,
or parameter values. The receipt stores only its schema version, fingerprint,
and the hashes of all four artifact files. Logs contain only stable error codes
and non-sensitive counts. The output directory is content-addressed by
canonical request plus recipe version at `jobs/<fingerprint>`; an unchanged
request replays only after the separate `.receipts/<fingerprint>.json` and all
artifact hashes verify. A changed request produces a new fingerprint. If an
existing artifact or receipt is tampered with, return `replay_integrity_error`
and never overwrite it.

The standard route uses Python/Pydantic and the standard-library `sqlite3`
parser only. HTTP use is local-only and the service is started bound to
`127.0.0.1`; no production or remote deployment is supported. No LLM,
provider, new package, remote data source, credential, audience, publishing,
or write-query operation is available in V1A. Local compute cost remains
unknown without an approved rate card. A later optional
intent-to-IR adapter must return this same validated contract and use explicit
budget, evaluation, and abstention controls; it is outside this design.

## Acceptance gates

- 20 synthetic requests cover valid graphs and expected rejections; all results
  match their fixture status, every accepted SQL plan passes in-memory
  `EXPLAIN QUERY PLAN` and records non-empty explain rows in the report, and
  there are zero unexpected cases.
- SQL is deterministic, read-only, parameterized, and contains no filter value
  as a SQL literal.
- Output schema, node order, and source lineage match the validated IR.
- Replays verify the separate receipt and every artifact hash; changed inputs produce a new fingerprint.
- `model_calls=0` for API, CLI, benchmark, and replay paths.
- No network access, external database connection, query execution, or
  publishing side effect occurs.
