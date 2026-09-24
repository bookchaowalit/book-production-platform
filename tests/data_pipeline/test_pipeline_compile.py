from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

import book_production_platform.data_pipeline.pipeline as pipeline_module
from book_production_platform.data_pipeline.pipeline import (
    PipelineExplainError,
    compile_pipeline_ir,
    explain_pipeline_sql,
    parameter_document,
)
from book_production_platform.data_pipeline_ir.models import CellValue, PipelineIR
from book_production_platform.data_pipeline_ir.validation import (
    PipelineValidationError,
    build_pipeline_ir,
)


def pipeline_fixture(
    filter_value: CellValue,
    *,
    data_type: str = "text",
    column_key: str = "region",
) -> PipelineIR:
    return build_pipeline_ir(
        {
            "pipeline_id": "filter-case",
            "source": {
                "table_name": "orders",
                "source_ref": "warehouse.orders",
                "columns": [{"key": column_key, "label": "Region", "data_type": data_type}],
            },
            "nodes": [
                {
                    "node_id": "filtered",
                    "parent_ids": ["source"],
                    "op": "filter_equals",
                    "column": column_key,
                    "value": filter_value,
                }
            ],
            "output_node": "filtered",
        }
    )


def test_filter_value_is_bound_not_interpolated() -> None:
    value = "BKK' OR 1=1 --"
    pipeline = pipeline_fixture(value)

    compiled = compile_pipeline_ir(pipeline)

    assert value not in compiled.sql
    assert compiled.sql.count("?") == 1
    assert compiled.parameters == [value]
    assert compiled.parameter_types == ["text"]
    assert compiled.model_calls == 0
    assert compiled.sql == compile_pipeline_ir(pipeline).sql


def test_parameter_document_preserves_decimal_date_and_boolean_types() -> None:
    pipeline = build_pipeline_ir(
        {
            "pipeline_id": "typed-parameters",
            "source": {
                "table_name": "observations",
                "source_ref": "local.observations",
                "columns": [
                    {"key": "amount", "label": "Amount", "data_type": "decimal"},
                    {"key": "as_of", "label": "As of", "data_type": "date"},
                    {"key": "active", "label": "Active", "data_type": "boolean"},
                ],
            },
            "nodes": [
                {"node_id": "amount-filter", "parent_ids": ["source"], "op": "filter_equals", "column": "amount", "value": Decimal("2.50")},
                {"node_id": "date-filter", "parent_ids": ["amount-filter"], "op": "filter_equals", "column": "as_of", "value": date(2026, 9, 24)},
                {"node_id": "active-filter", "parent_ids": ["date-filter"], "op": "filter_equals", "column": "active", "value": True},
            ],
            "output_node": "active-filter",
        }
    )

    compiled = compile_pipeline_ir(pipeline)

    assert compiled.parameters == [Decimal("2.50"), date(2026, 9, 24), True]
    assert compiled.parameter_types == ["decimal", "date", "boolean"]
    assert parameter_document(compiled) == {
        "schema_version": "pipeline-parameters.v1",
        "parameters": [
            {"type": "decimal", "value": "2.50"},
            {"type": "date", "value": "2026-09-24"},
            {"type": "boolean", "value": True},
        ],
    }


def test_limit_uses_stable_tie_breakers_in_limit_and_final_select() -> None:
    pipeline = build_pipeline_ir(
        {
            "pipeline_id": "stable-limit",
            "source": {
                "table_name": "scores",
                "source_ref": "local.scores",
                "columns": [
                    {"key": "label", "label": "Label", "data_type": "text"},
                    {"key": "score", "label": "Score", "data_type": "decimal"},
                    {"key": "id", "label": "ID", "data_type": "integer"},
                ],
            },
            "nodes": [
                {"node_id": "limited", "parent_ids": ["ordered"], "op": "limit", "limit": 25},
                {"node_id": "ordered", "parent_ids": ["source"], "op": "sort_by", "column": "score", "direction": "desc"},
            ],
            "output_node": "limited",
        }
    )

    compiled = compile_pipeline_ir(pipeline)

    stable_order = 'ORDER BY "score" DESC, "id" ASC, "label" ASC'
    assert compiled.sql.count(stable_order) == 2
    assert compiled.parameters == [25]
    assert compiled.parameter_types == ["integer"]
    assert parameter_document(compiled)["parameters"] == [{"type": "integer", "value": 25}]
    assert explain_pipeline_sql(pipeline, compiled).steps


def test_sort_output_orders_only_by_the_requested_key() -> None:
    pipeline = build_pipeline_ir(
        {
            "pipeline_id": "sort-output",
            "source": {
                "table_name": "orders",
                "source_ref": "warehouse.orders",
                "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
            },
            "nodes": [{"node_id": "ordered", "parent_ids": ["source"], "op": "sort_by", "column": "region", "direction": "asc"}],
            "output_node": "ordered",
        }
    )

    compiled = compile_pipeline_ir(pipeline)

    assert compiled.sql.endswith('ORDER BY "region" ASC')
    assert compiled.parameters == []


def test_union_can_use_the_declared_source_as_a_parent() -> None:
    pipeline = build_pipeline_ir(
        {
            "pipeline_id": "union-source-parent",
            "source": {
                "table_name": "orders",
                "source_ref": "warehouse.orders",
                "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
            },
            "nodes": [
                {"node_id": "projected", "parent_ids": ["source"], "op": "select", "columns": ["region"]},
                {"node_id": "combined", "parent_ids": ["source", "projected"], "op": "union_all"},
            ],
            "output_node": "combined",
        }
    )

    compiled = compile_pipeline_ir(pipeline)

    assert 'SELECT * FROM "orders" UNION ALL SELECT * FROM "step_0"' in compiled.sql


def test_supported_graph_ops_and_unicode_schema_compile_to_quoted_sql() -> None:
    pipeline = build_pipeline_ir(
        {
            "pipeline_id": "unicode-ops",
            "source": {
                "table_name": "orders",
                "source_ref": "warehouse.orders",
                "columns": [
                    {"key": "地域", "label": "営業地域", "data_type": "text"},
                    {"key": "売上", "label": "売上金額", "data_type": "decimal"},
                ],
            },
            "nodes": [
                {"node_id": "left-filter", "parent_ids": ["source"], "op": "filter_null", "column": "地域"},
                {"node_id": "left", "parent_ids": ["left-filter"], "op": "select", "columns": ["地域", "売上"]},
                {"node_id": "right-filter", "parent_ids": ["source"], "op": "filter_equals", "column": "地域", "value": "東京"},
                {"node_id": "right", "parent_ids": ["right-filter"], "op": "select", "columns": ["地域", "売上"]},
                {"node_id": "combined", "parent_ids": ["left", "right"], "op": "union_all"},
                {
                    "node_id": "summary",
                    "parent_ids": ["combined"],
                    "op": "aggregate",
                    "group_by": ["地域"],
                    "metrics": [
                        {"alias": "rows", "op": "count"},
                        {"alias": "gross", "op": "sum", "column": "売上"},
                        {"alias": "average", "op": "avg", "column": "売上"},
                    ],
                },
            ],
            "output_node": "summary",
        }
    )

    compiled = compile_pipeline_ir(pipeline)

    assert '"地域" IS NULL' in compiled.sql
    assert '"地域" = ?' in compiled.sql
    assert '"売上"' in compiled.sql
    assert "UNION ALL" in compiled.sql
    assert 'COUNT(*) AS "rows"' in compiled.sql
    assert 'SUM("売上") AS "gross"' in compiled.sql
    assert 'AVG("売上") AS "average"' in compiled.sql
    assert compiled.parameters == ["東京"]
    assert compiled.output_columns[0].label == "営業地域"
    assert explain_pipeline_sql(pipeline, compiled).steps


def test_explain_uses_only_memory_schema_and_never_runs_pipeline_select(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = pipeline_fixture("secret-value")
    compiled = compile_pipeline_ir(pipeline)
    original_connect = sqlite3.connect
    connection = original_connect(":memory:")
    traced_statements: list[str] = []
    connection.set_trace_callback(traced_statements.append)
    monkeypatch.setattr(pipeline_module.sqlite3, "connect", lambda *args, **kwargs: connection)

    result = explain_pipeline_sql(pipeline, compiled)

    assert result.sqlite_version == sqlite3.sqlite_version
    assert result.steps
    assert all(
        statement.lstrip().upper().startswith(("CREATE TABLE", "EXPLAIN QUERY PLAN"))
        for statement in traced_statements
    )
    assert not any(statement.lstrip().upper().startswith("SELECT") for statement in traced_statements)


def test_explain_errors_have_a_stable_non_sensitive_code(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = pipeline_fixture("private-value")
    compiled = compile_pipeline_ir(pipeline)

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        del args, kwargs
        raise sqlite3.OperationalError("private SQL details")

    monkeypatch.setattr(pipeline_module.sqlite3, "connect", fail_connect)

    with pytest.raises(PipelineExplainError) as error:
        explain_pipeline_sql(pipeline, compiled)

    assert error.value.code == "pipeline_sql_invalid"
    assert str(error.value) == "pipeline_sql_invalid"
    assert "private SQL details" not in str(error.value)


def test_missing_input_column_is_rejected_before_compile() -> None:
    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(
            {
                "pipeline_id": "unknown-column",
                "source": {
                    "table_name": "orders",
                    "source_ref": "warehouse.orders",
                    "columns": [{"key": "region", "label": "Region", "data_type": "text"}],
                },
                "nodes": [{"node_id": "filtered", "parent_ids": ["source"], "op": "filter_null", "column": "missing"}],
                "output_node": "filtered",
            }
        )

    assert [issue.code for issue in error.value.issues] == ["unknown_pipeline_column"]
