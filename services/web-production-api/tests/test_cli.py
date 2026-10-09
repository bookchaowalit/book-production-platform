from __future__ import annotations

import json
from pathlib import Path

from web_production.cli import main


def test_cli_build_prints_json_result(tmp_path: Path, capsys) -> None:
    payload = {"page": {"page_id": "cli-01", "slug": "cli-page", "title": "CLI page", "description": "desc", "hero_heading": "Hello", "hero_body": "Body", "hero_cta": {"label": "Start", "href": "#signup"}, "features": [{"title": "One", "body": "First"}]}}
    input_path = tmp_path / "page.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"


def test_cli_rejects_non_object_input(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "page.json"
    input_path.write_text("[]", encoding="utf-8")

    exit_code = main(["build", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-key"])

    assert exit_code == 2
    assert "page" in capsys.readouterr().err
