import json
from pathlib import Path

import pytest

from book_production_platform import cli
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.jobs import JobResult
from tests.test_scene_ir import valid_scene


def test_cli_help_describes_scene_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])

    assert error.value.code == 0
    assert "scene" in capsys.readouterr().out


def test_cli_invalid_scene_returns_stable_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = tmp_path / "scene.json"
    scene.write_text('{"schema_version":"wrong"}', encoding="utf-8")

    exit_code = cli.main(
        [
            "scene",
            "compose",
            "--scene",
            str(scene),
            "--input-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path / "output"),
            "--blender",
            str(tmp_path / "blender.exe"),
        ]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_scene_ir",
        "status": "rejected",
    }


@pytest.mark.parametrize(
    "suffix",
    [
        b',"nested":' + b"[" * 10000 + b"null" + b"]" * 10000 + b"}",
        b',"oversized":' + b"9" * 5000 + b"}",
    ],
    ids=["deeply-nested-json", "oversized-integer"],
)
def test_cli_malformed_scene_json_returns_stable_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: bytes,
) -> None:
    scene = tmp_path / "scene.json"
    scene.write_bytes(json.dumps(valid_scene()).encode("utf-8")[:-1] + suffix)

    exit_code = cli.main(
        [
            "scene",
            "compose",
            "--scene",
            str(scene),
            "--input-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path / "output"),
            "--blender",
            str(tmp_path / "blender.exe"),
        ]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_scene_ir",
        "status": "rejected",
    }
    assert not (tmp_path / "output").exists()


def test_cli_accepted_job_returns_json_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = JobResult(
        status="accepted",
        fingerprint="a" * 64,
        artifact_dir=tmp_path / "artifact",
        artifact_hashes={"scene.blend": "b" * 64},
        replay=False,
        model_calls=0,
    )
    monkeypatch.setattr(cli, "compose_scene", lambda *_args, **_kwargs: result)

    exit_code = cli.main(
        [
            "scene",
            "compose",
            "--scene",
            str(tmp_path / "scene.json"),
            "--input-root",
            str(tmp_path / "input"),
            "--output-root",
            str(tmp_path / "output"),
            "--blender",
            str(tmp_path / "blender.exe"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "accepted"
    assert output["model_calls"] == 0
    assert output["artifact_dir"] == str(result.artifact_dir)


def test_cli_reports_unsupported_runtime_with_stable_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unsupported(*_args: object, **_kwargs: object) -> JobResult:
        raise SceneInputError("unsupported_blender_version")

    monkeypatch.setattr(cli, "compose_scene", unsupported)
    exit_code = cli.main(
        [
            "scene",
            "compose",
            "--scene",
            str(tmp_path / "scene.json"),
            "--input-root",
            str(tmp_path / "input"),
            "--output-root",
            str(tmp_path / "output"),
            "--blender",
            str(tmp_path / "blender.exe"),
        ]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "unsupported_blender_version",
        "status": "rejected",
    }


def test_cli_benchmark_returns_machine_readable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {"status": "passed", "cases_total": 20, "model_calls": 0}
    monkeypatch.setattr(cli, "run_benchmark", lambda *_args, **_kwargs: report)

    exit_code = cli.main(
        [
            "scene",
            "benchmark",
            "--fixtures",
            str(tmp_path / "fixtures"),
            "--output",
            str(tmp_path / "benchmark.json"),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == report
