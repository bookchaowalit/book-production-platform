from __future__ import annotations

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
