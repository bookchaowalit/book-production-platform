import json
from pathlib import Path

from book_production_platform.cli import main as platform_main
from book_production_platform.graphics_production.cli import main
from tests.graphics.browser import requires_browser
from tests.graphics.test_jobs import item


@requires_browser
def test_cli_batch_prints_json_summary(tmp_path: Path, capsys: object) -> None:
    input_path = tmp_path / "batch.json"
    input_path.write_text(json.dumps({"items": [item()]}, ensure_ascii=False), encoding="utf-8")

    exit_code = main(["batch", "--input", str(input_path), "--output", str(tmp_path / "out"), "--idempotency-key", "cli-1"])

    assert exit_code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(captured.out)["status"] == "accepted"


@requires_browser
def test_platform_cli_routes_graphics_batch(tmp_path: Path, capsys: object) -> None:
    input_path = tmp_path / "batch.json"
    input_path.write_text(json.dumps({"items": [item()]}, ensure_ascii=False), encoding="utf-8")

    exit_code = platform_main([
        "graphics", "batch", "--input", str(input_path), "--output", str(tmp_path / "platform-out"),
        "--idempotency-key", "platform-cli-1",
    ])

    assert exit_code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(captured.out)["status"] == "accepted"
