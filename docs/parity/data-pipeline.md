# Data Pipeline V1A — feature parity with Power Query, dbt, and Metabase

Scope: `data_pipeline` + `data_pipeline_ir` (tool id `data-pipeline-v1a`). The
engine validates a typed `pipeline-request.v1` operation graph over **one**
source table, compiles it into one parameterized, read-only SQLite `SELECT`
(CTE per node), verifies it with an in-memory `EXPLAIN QUERY PLAN`, and
publishes content-addressed plan artifacts. Production runs are plan-only:
the engine never connects to a real database. This change adds an opt-in
local execution over caller-supplied rows.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope.

## Capability matrix

| Capability | Power Query / dbt / Metabase | Engine status | Priority | Notes |
|---|---|---|---|---|
| Choose / reorder columns | Choose Columns / `select` / question columns | have | — | `select` |
| Filter equals | Filter ▸ Equals / `where =` / filter | have (fixed) | — | `filter_equals`, bound parameter; **fixed in this change**: a decimal value compared with an aggregate output (no column affinity) matched nothing because the parameter is bound as exact text — the compiler now emits `CAST(? AS NUMERIC)` for decimal parameters |
| Filter is null | Filter ▸ (null) / `is null` | have | — | `filter_null` |
| Filter comparisons (`>`, `>=`, `<`, `<=`, `<>`) | Number/Date Filters / `where` / filter | **have (new)** | — | `filter_compare`; ordered comparisons on integer/decimal/date, `ne` on any type; NULL rows excluded (SQL semantics); decimal parameters use `CAST(? AS NUMERIC)` so comparisons after aggregates stay numeric |
| Remove duplicates | Remove Duplicates / `distinct` | **have (new)** | — | `distinct` (`SELECT DISTINCT *`) |
| Group by + count / sum / avg | Group By / `group by` / summarize | have | — | `aggregate`; `count(col)` counts non-null |
| Sort + top N (deterministic) | Sort + Keep Top Rows / `order by … limit` | have | — | `sort_by` + terminal `limit`, with deterministic tie-breakers |
| Append (union all) | Append Queries / `union all` | have | — | `union_all`, schemas must match |
| Column lineage | Query Dependencies / dbt lineage / — | have | — | per node and output column |
| Parameterized SQL, no value interpolation | — / Jinja / — | have | — | values only in `parameters.json` |
| Execute and export results (CSV) | Close & Load / `dbt run` + export / download CSV | **have (new, opt-in)** | — | `run_pipeline_execution_job` / `book-production data pipeline run --rows`: plan job first, then the same SQL read-only (`PRAGMA query_only`) in memory over the supplied rows → `results.csv` + `execution-manifest.json`; formula-like text is prefixed with `'` in the CSV |
| Job trace, history, Power Query manual | Applied Steps history / dbt docs / — | **have (new)** | — | `trace.json`, `history.jsonl`, `ir.json`, `MANUAL-STEPS.md` (Applied Steps + M + SQL for DB Browser / DBeaver) |
| Join (two sources) | Merge Queries / `join` / joins | missing | P1 | the IR has one `source`; needs multi-source `PipelineSource`, key-type checks, join-type allowlist, lineage merge, and V1B allowlist + eval cases. Kept `join_not_supported` in the proposal check |
| Derived / calculated columns | Add Column ▸ Custom / expressions / custom column | missing | P1 | needs a small, allowlisted expression IR (arithmetic, `coalesce`, `case`) compiled to SQL, not free text |
| Rename columns | Rename / `as` | missing | P1 | only metric aliases today |
| Multi-key sort, `IN` lists, `between`, text contains / starts with | yes | missing | P2 | `LIKE` needs escaping rules; `IN` needs list parameters |
| More aggregates (`min`, `max`, `count distinct`, median) | yes | missing | P2 | |
| Real database connectors | Get Data / adapters / drivers | — | — | V1A is deliberately plan-only; execution only over rows the caller already holds |
| XLSX export of results | Close & Load to sheet | missing | P2 | could reuse `spreadsheet_production`; CSV only today |
| Incremental models, tests, docs site | — / dbt / — | missing | P3 | |
| Dashboards, charts, scheduling | — / — / Metabase | — | — | out of scope |
| AI question → pipeline proposal (V1B) | — | have | — | **allowlist updated**: steps `distinct` and `filter` with `operator` (`eq`, `gt`, `gte`, `lt`, `lte`, `ne`); the provider prompt lists them; filter values must still be grounded in the question |

## Implemented in this change

1. `filter_compare` node (IR, validation, lineage, compiler, report, manual, proposal allowlist).
2. `distinct` node (IR, compiler, report, manual, proposal allowlist).
3. Opt-in local execution with CSV export (`execute.py`, CLI `pipeline run`).
4. Fix: decimal `filter_equals` after an `aggregate` returned no rows; decimal
   parameters are now compared through `CAST(? AS NUMERIC)`. Already published
   jobs still replay (replay re-hashes the stored files); only a crash-recovery
   rebuild of such an old job would now fail closed because its SQL changed.
5. Requests without the new `operator` field keep their plan fingerprint, so
   earlier published jobs still replay.

The trace sidecars (`trace.json`, `MANUAL-STEPS.md`, `ir.json`) are written
into the published job directory after the receipt; they are not covered by
the receipt and the artifact-set checks ignore exactly those names (any other
extra file still fails replay).

## Roadmap (prioritised)

1. P1 — two-source `join` (inner/left) with key-type checks and lineage, then the V1B `join` step.
2. P1 — allowlisted derived-column expressions and column rename.
3. P2 — `IN` / `between` / text-pattern filters with safe escaping; `min` / `max` / `count distinct`.
4. P2 — XLSX export of results through the spreadsheet engine.
5. P3 — dbt-style tests (unique / not null) over execution results.
