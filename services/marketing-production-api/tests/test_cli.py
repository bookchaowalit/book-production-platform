from __future__ import annotations

import json
from pathlib import Path

from marketing_production.cli import main


def test_cli_build_prints_json_result(tmp_path: Path, capsys) -> None:
    payload = {
        "campaign": {
            "campaign_id": "cli-01",
            "name": "CLI campaign",
            "objective": "awareness",
            "channels": ["email"],
            "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "en"}]},
            "assets": [{"asset_id": "copy", "kind": "copy", "channel": "email", "uri": "inline:copy", "text": "hello"}],
        }
    }
    input_path = tmp_path / "campaign.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"


def test_cli_rejects_non_object_input(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "campaign.json"
    input_path.write_text("[]", encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 2
    assert "campaign" in capsys.readouterr().err
