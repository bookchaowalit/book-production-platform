from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from book_production_platform.data_pipeline_ir.validation import (
    PipelineValidationError,
    build_pipeline_ir,
)


def column(key: str, data_type: str = "text", **changes: Any) -> dict[str, Any]:
    return {"key": key, "label": key, "data_type": data_type, **changes}


def request(
    nodes: list[dict[str, Any]],
    output_node: str,
    *,
    columns: list[dict[str, Any]] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "pipeline-request.v1",
        "pipeline_id": "pipeline-test",
        "source": {
            "table_name": "orders",
            "source_ref": "warehouse.orders",
            "columns": columns
            if columns is not None
            else [
                column("region"),
                column("amount", "decimal"),
                column("units", "integer"),
                column("created", "date"),
                column("active", "boolean"),
            ],
        },
        "nodes": nodes,
        "output_node": output_node,
        **changes,
    }


def codes(error: PipelineValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_graph_rejects_cycle_and_unknown_parent() -> None:
    cyclic = request(
        [
            {"node_id": "a", "parent_ids": ["b"], "op": "filter_null", "column": "region"},
            {"node_id": "b", "parent_ids": ["a"], "op": "filter_null", "column": "region"},
        ],
        "b",
    )
    unknown = request(
        [{"node_id": "a", "parent_ids": ["missing"], "op": "filter_null", "column": "region"}],
        "a",
    )

    with pytest.raises(PipelineValidationError) as cycle_error:
        build_pipeline_ir(cyclic)
    with pytest.raises(PipelineValidationError) as parent_error:
        build_pipeline_ir(unknown)

    assert "pipeline_cycle" in codes(cycle_error.value)
    assert "unknown_pipeline_parent" in codes(parent_error.value)


def test_ready_nodes_have_stable_topological_order_and_union_schema() -> None:
    pipeline = request(
        [
            {"node_id": "z_branch", "parent_ids": ["source"], "op": "select", "columns": ["region", "amount"]},
            {"node_id": "a_branch", "parent_ids": ["source"], "op": "select", "columns": ["region", "amount"]},
            {"node_id": "combined", "parent_ids": ["a_branch", "z_branch"], "op": "union_all"},
        ],
        "combined",
    )

    pipeline_ir = build_pipeline_ir(pipeline)

    assert [node.node_id for node in pipeline_ir.ordered_nodes] == ["a_branch", "z_branch", "combined"]
    assert [(item.key, item.data_type) for item in pipeline_ir.output_columns] == [
        ("region", "text"),
        ("amount", "decimal"),
    ]
    assert pipeline_ir.output_lineage["region"][0].source_ref == "warehouse.orders"
    assert pipeline_ir.output_lineage["amount"][0].column_key == "amount"


def test_projection_and_filter_preserve_typed_schema_and_lineage() -> None:
    pipeline = request(
        [
            {"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": "BKK"},
            {"node_id": "selected", "parent_ids": ["filtered"], "op": "select", "columns": ["amount", "region"]},
        ],
        "selected",
    )

    pipeline_ir = build_pipeline_ir(pipeline)

    assert [(item.key, item.data_type) for item in pipeline_ir.output_columns] == [
        ("amount", "decimal"),
        ("region", "text"),
    ]
    assert pipeline_ir.lineage_by_node["filtered"]["region"][0].table_name == "orders"
    assert pipeline_ir.output_lineage["region"][0].column_key == "region"
    assert pipeline_ir.source_refs == ["warehouse.orders"]


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("text", "BKK"),
        ("integer", 7),
        ("decimal", 7),
        ("decimal", Decimal("7.25")),
        ("date", "2026-09-24"),
        ("date", date(2026, 9, 24)),
        ("boolean", True),
    ],
)
def test_filter_values_match_declared_types_without_coercion(data_type: str, value: Any) -> None:
    pipeline = request(
        [{"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "value", "value": value}],
        "filtered",
        columns=[column("value", data_type)],
    )

    pipeline_ir = build_pipeline_ir(pipeline)

    if data_type == "date" and isinstance(value, str):
        assert pipeline_ir.ordered_nodes[0].value == date(2026, 9, 24)
    else:
        assert pipeline_ir.ordered_nodes[0].value == value


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("text", 7),
        ("integer", True),
        ("integer", "7"),
        ("decimal", "7.25"),
        ("decimal", Decimal("NaN")),
        ("date", "09/24/2026"),
        ("boolean", 1),
        ("boolean", "true"),
        ("text", ["BKK"]),
    ],
)
def test_filter_values_with_wrong_types_are_rejected(data_type: str, value: Any) -> None:
    pipeline = request(
        [{"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "value", "value": value}],
        "filtered",
        columns=[column("value", data_type)],
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "pipeline_type_mismatch" in codes(error.value)


def test_filter_null_and_sort_by_preserve_parent_schema() -> None:
    pipeline_ir = build_pipeline_ir(
        request(
            [
                {"node_id": "not_null", "parent_ids": ["source"], "op": "filter_null", "column": "region"},
                {"node_id": "ordered", "parent_ids": ["not_null"], "op": "sort_by", "column": "region", "direction": "desc"},
            ],
            "ordered",
        )
    )

    assert pipeline_ir.output_columns == pipeline_ir.schemas_by_node["ordered"]
    assert pipeline_ir.ordered_nodes[-1].direction == "desc"


def test_aggregate_schema_and_lineage_cover_count_sum_and_average() -> None:
    pipeline = request(
        [
            {
                "node_id": "summary",
                "parent_ids": ["source"],
                "op": "aggregate",
                "group_by": ["region"],
                "metrics": [
                    {"alias": "row_count", "op": "count"},
                    {"alias": "amount_sum", "op": "sum", "column": "amount"},
                    {"alias": "unit_average", "op": "avg", "column": "units"},
                ],
            }
        ],
        "summary",
    )

    pipeline_ir = build_pipeline_ir(pipeline)

    assert [(item.key, item.data_type) for item in pipeline_ir.output_columns] == [
        ("region", "text"),
        ("row_count", "integer"),
        ("amount_sum", "decimal"),
        ("unit_average", "decimal"),
    ]
    assert pipeline_ir.output_lineage["row_count"][0].column_key is None
    assert pipeline_ir.output_lineage["amount_sum"][0].column_key == "amount"
    assert pipeline_ir.output_lineage["unit_average"][0].column_key == "units"


def test_total_aggregate_without_grouping_counts_rows() -> None:
    pipeline_ir = build_pipeline_ir(
        request(
            [{"node_id": "summary", "parent_ids": ["source"], "op": "aggregate", "group_by": [], "metrics": [{"alias": "rows", "op": "count"}]}],
            "summary",
        )
    )

    assert [(item.key, item.data_type) for item in pipeline_ir.output_columns] == [("rows", "integer")]
    assert pipeline_ir.output_lineage["rows"][0].column_key is None


@pytest.mark.parametrize(
    ("metric", "expected_code"),
    [
        ({"alias": "bad_sum", "op": "sum", "column": "region"}, "pipeline_type_mismatch"),
        ({"alias": "bad_avg", "op": "avg", "column": "active"}, "pipeline_type_mismatch"),
        ({"alias": "bad_count", "op": "count", "column": "missing"}, "unknown_pipeline_column"),
    ],
)
def test_aggregate_rejects_invalid_metric_inputs(metric: dict[str, Any], expected_code: str) -> None:
    pipeline = request(
        [{"node_id": "summary", "parent_ids": ["source"], "op": "aggregate", "group_by": [], "metrics": [metric]}],
        "summary",
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert expected_code in codes(error.value)


def test_aggregate_aliases_cannot_collide_with_group_keys_or_each_other() -> None:
    pipeline = request(
        [
            {
                "node_id": "summary",
                "parent_ids": ["source"],
                "op": "aggregate",
                "group_by": ["region"],
                "metrics": [
                    {"alias": "region", "op": "count"},
                    {"alias": "rows", "op": "count"},
                    {"alias": "rows", "op": "count", "column": "units"},
                ],
            }
        ],
        "summary",
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "invalid_pipeline_operation_fields" in codes(error.value)


def test_union_requires_the_same_ordered_keys_and_types() -> None:
    pipeline = request(
        [
            {"node_id": "left", "parent_ids": ["source"], "op": "select", "columns": ["region", "amount"]},
            {"node_id": "right", "parent_ids": ["source"], "op": "select", "columns": ["amount", "region"]},
            {"node_id": "combined", "parent_ids": ["left", "right"], "op": "union_all"},
        ],
        "combined",
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "union_schema_mismatch" in codes(error.value)


@pytest.mark.parametrize(
    "pipeline",
    [
        request(
            [
                {"node_id": "sorted", "parent_ids": ["source"], "op": "sort_by", "column": "region"},
                {"node_id": "limited", "parent_ids": ["sorted"], "op": "limit", "limit": 5},
                {"node_id": "selected", "parent_ids": ["limited"], "op": "select", "columns": ["region"]},
            ],
            "selected",
        ),
        request(
            [{"node_id": "limited", "parent_ids": ["source"], "op": "limit", "limit": 5}],
            "limited",
        ),
        request(
            [
                {"node_id": "sorted", "parent_ids": ["source"], "op": "sort_by", "column": "region"},
                {"node_id": "selected", "parent_ids": ["sorted"], "op": "select", "columns": ["region"]},
            ],
            "selected",
        ),
    ],
)
def test_row_limiting_requires_a_terminal_sorted_output(pipeline: dict[str, Any]) -> None:
    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "nondeterministic_limit" in codes(error.value)


def test_terminal_sort_limit_uses_a_known_sort_key() -> None:
    pipeline = request(
        [
            {"node_id": "sorted", "parent_ids": ["source"], "op": "sort_by", "column": "missing", "direction": "asc"},
            {"node_id": "limited", "parent_ids": ["sorted"], "op": "limit", "limit": 5},
        ],
        "limited",
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "unknown_pipeline_column" in codes(error.value)


@pytest.mark.parametrize(
    ("nodes", "output_node", "expected"),
    [
        ([{"node_id": "a", "parent_ids": ["source"], "op": "filter_null", "column": "region"}, {"node_id": "a", "parent_ids": ["source"], "op": "filter_null", "column": "region"}], "a", "duplicate_pipeline_node_id"),
        ([{"node_id": "a", "parent_ids": ["source", "source"], "op": "union_all"}], "a", "duplicate_pipeline_parent"),
        ([{"node_id": "source", "parent_ids": ["source"], "op": "filter_null", "column": "region"}], "source", "reserved_pipeline_node_id"),
        ([{"node_id": "a", "parent_ids": ["source"], "op": "filter_null", "column": "region"}], "missing", "unknown_output_node"),
        ([{"node_id": "a", "parent_ids": ["source"], "op": "filter_null", "column": "region"}, {"node_id": "unused", "parent_ids": ["source"], "op": "filter_null", "column": "region"}], "a", "pipeline_dead_node"),
        ([{"node_id": "a", "parent_ids": ["source"], "op": "filter_null", "column": "missing"}], "a", "unknown_pipeline_column"),
    ],
)
def test_graph_schema_errors_have_stable_codes(
    nodes: list[dict[str, Any]], output_node: str, expected: str
) -> None:
    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(request(nodes, output_node))

    assert expected in codes(error.value)


@pytest.mark.parametrize(
    "node",
    [
        {"node_id": "selected", "parent_ids": ["source"], "op": "select"},
        {"node_id": "selected", "parent_ids": ["source"], "op": "select", "columns": []},
        {"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "region"},
        {"node_id": "filtered", "parent_ids": ["source"], "op": "filter_equals", "column": "region", "value": None},
        {"node_id": "filtered", "parent_ids": ["source"], "op": "filter_null", "column": "region", "value": None},
        {"node_id": "limited", "parent_ids": ["source"], "op": "limit", "limit": True},
        {"node_id": "summary", "parent_ids": ["source"], "op": "aggregate", "group_by": [], "metrics": []},
        {"node_id": "selected", "parent_ids": ["source"], "op": "select", "columns": ["region"], "direction": "asc"},
    ],
)
def test_operation_fields_are_required_and_irrelevant_fields_are_rejected(node: dict[str, Any]) -> None:
    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(request([node], node["node_id"]))

    assert "invalid_pipeline_operation_fields" in codes(error.value)


@pytest.mark.parametrize(
    "columns",
    [
        [column("region"), column("region", "integer")],
        [column("region", source_name="same"), column("amount", "decimal", source_name="same")],
        [column("bad key")],
        [column("region", label="")],
        [column("region", label="x" * 121)],
        [column("region", source_name="x" * 129)],
        [column("x" * 64)],
    ],
)
def test_invalid_source_schema_is_reported_separately(columns: list[dict[str, Any]]) -> None:
    pipeline = request(
        [{"node_id": "selected", "parent_ids": ["source"], "op": "select", "columns": [columns[0]["key"]]}],
        "selected",
        columns=columns,
    )

    with pytest.raises(PipelineValidationError) as error:
        build_pipeline_ir(pipeline)

    assert "invalid_pipeline_source_schema" in codes(error.value)


def test_invalid_request_and_oversized_input_have_stable_codes() -> None:
    with pytest.raises(PipelineValidationError) as request_error:
        build_pipeline_ir({"pipeline_id": "invalid", "unknown": "field"})
    oversized = {"pipeline_id": "large", "unknown": "x" * 262_145}
    with pytest.raises(PipelineValidationError) as size_error:
        build_pipeline_ir(oversized)

    assert "invalid_pipeline_request" in codes(request_error.value)
    assert "pipeline_request_too_large" in codes(size_error.value)
