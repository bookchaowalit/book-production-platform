from __future__ import annotations

import json
from pathlib import Path

from workflow_production.cli import main


def _workflow() -> dict:
    return {
        "workflow_id": "cli-01",
        "name": "Onboarding",
        "fields": [{"field_id": "customer_name", "label": "Customer", "field_type": "text", "required": True}],
        "form_values": {"customer_name": "Ada"},
        "steps": [{"step_id": "collect", "kind": "collect_form"}, {"step_id": "render", "kind": "render_document", "depends_on": ["collect"]}],
        "document": {"title": "Brief", "sections": [{"kind": "field", "field_id": "customer_name"}]},
        "permissions": {"allowed_roles": ["ops"]},
    }


def test_cli_build_prints_json_result(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "workflow.json"
    input_path.write_text(json.dumps({"workflow": _workflow()}), encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"


def test_cli_rejects_non_object_input(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "workflow.json"
    input_path.write_text("[]", encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 2
    assert "workflow" in capsys.readouterr().err
