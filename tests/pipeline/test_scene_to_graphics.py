"""Unit tests for the scene-to-graphics pipeline with faked stages."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

import book_production_platform.cli as platform_cli
from book_production_platform.graphics_ir import ArtifactRef
from book_production_platform.graphics_production.jobs import BatchResult, ItemResult
from book_production_platform.pipeline import cli as pipeline_cli
from book_production_platform.pipeline import jobs as pipeline_jobs
from book_production_platform.pipeline.errors import PipelineError
from book_production_platform.pipeline.jobs import (
    CONTRACT_FILENAME,
    MANIFEST_FILENAME,
    PIPELINE_DIRECTORY,
    PipelineResult,
    run_scene_to_graphics,
)
from book_production_platform.pipeline.models import (
    CONTRACT_SCHEMA_VERSION,
    parse_scene_render_batch,
    read_graphics_spec,
)
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.jobs import JobResult, sha256_file
from book_production_platform.scene3d.scene_builder import RENDER_PROFILES

FINGERPRINT = "a" * 64
BATCH_ID = "b" * 20


def spec_value(**overrides: object) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "scene-graphics-spec.v1",
        "template_id": "product_card",
        "variants": ["square", "story"],
        "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
        "content": {"title": "แก้วเก็บอุณหภูมิ", "price": "฿390"},
    }
    value.update(overrides)
    return value


def write_spec(root: Path, value: dict[str, Any] | None = None) -> Path:
    path = root / "spec.json"
    path.write_text(json.dumps(value if value is not None else spec_value()), encoding="utf-8")
    return path


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (225, 230, 235))
    ImageDraw.Draw(image).rectangle((30, 30, width // 2, height // 2), fill=(20, 80, 150))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def publish_scene_job(
    output_root: Path, *, fingerprint: str = FINGERPRINT, profile: str = "square-1024.v1"
) -> Path:
    """Materialize one accepted scene3d job shaped like the real composer output."""

    width, height = RENDER_PROFILES[profile]
    job_dir = output_root / fingerprint
    job_dir.mkdir(parents=True, exist_ok=True)
    blend_path = job_dir / "scene.blend"
    blend_path.write_bytes(b"synthetic-blend\0" + profile.encode())
    render_path = job_dir / "render.png"
    render_path.write_bytes(_png_bytes(width, height))
    manifest = {
        "schema_version": "scene3d.qa.v1",
        "status": "accepted",
        "fingerprint": fingerprint,
        "render": {"engine": "EEVEE", "profile": profile, "size": [width, height], "eevee_samples": 16},
        "outputs": {
            "scene.blend": {
                "sha256": sha256_file(blend_path),
                "bytes": blend_path.stat().st_size,
            },
            "render.png": {
                "sha256": sha256_file(render_path),
                "bytes": render_path.stat().st_size,
                "width": width,
                "height": height,
            },
        },
    }
    (job_dir / "qa-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return job_dir


def install_fakes(
    monkeypatch: pytest.MonkeyPatch, *, batch_status: str = "accepted"
) -> dict[str, Any]:
    """Replace both stages with bounded fakes and record their inputs."""

    recorded: dict[str, Any] = {"compose": 0, "batch": 0, "items": None}

    def fake_compose(
        scene_path: Path,
        input_root: Path,
        output_root: Path,
        blender_bin: Path,
        *,
        timeout_s: int,
    ) -> JobResult:
        assert timeout_s > 0
        recorded["compose"] += 1
        job_dir = publish_scene_job(Path(output_root))
        return JobResult(
            "accepted", FINGERPRINT, job_dir.resolve(), {"render.png": "0" * 64}, False, 0
        )

    def fake_run_batch(
        items: list[dict[str, Any]], output_dir: str | Path, idempotency_key: str
    ) -> BatchResult:
        recorded["batch"] += 1
        recorded["items"] = items
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        item_results: list[ItemResult] = []
        for item in items:
            canvas = item["canvas"]
            assert isinstance(canvas, dict)
            png_path = output / f"{item['request_id']}.png"
            svg_path = output / f"{item['request_id']}.svg"
            png_path.write_bytes(b"png-bytes")
            svg_path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
            item_results.append(
                ItemResult(
                    request_id=str(item["request_id"]),
                    status="accepted",
                    artifacts=(
                        ArtifactRef(
                            filename=svg_path.name,
                            media_type="image/svg+xml",
                            content_hash="0" * 64,
                            byte_size=svg_path.stat().st_size,
                            width=int(canvas["width"]),
                            height=int(canvas["height"]),
                        ),
                        ArtifactRef(
                            filename=png_path.name,
                            media_type="image/png",
                            content_hash="0" * 64,
                            byte_size=png_path.stat().st_size,
                            width=int(canvas["width"]),
                            height=int(canvas["height"]),
                        ),
                    ),
                )
            )
        manifest_path = output / f"manifest-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:16]}.json"
        manifest_path.write_text(
            json.dumps({"batch_id": BATCH_ID, "status": batch_status}), encoding="utf-8"
        )
        return BatchResult(
            batch_id=BATCH_ID,
            status=batch_status,
            idempotency_key=idempotency_key,
            input_hash="0" * 64,
            items=tuple(item_results),
            manifest_path=manifest_path,
            model_calls=0,
            fresh_count=len(item_results) if batch_status == "accepted" else 0,
            replay_count=0,
            bytes_written=0,
        )

    monkeypatch.setattr(pipeline_jobs, "compose_scene", fake_compose)
    monkeypatch.setattr(pipeline_jobs, "run_batch", fake_run_batch)
    return recorded


def run_pipeline(tmp_path: Path, *, spec: dict[str, Any] | None = None) -> PipelineResult:
    return run_scene_to_graphics(
        tmp_path / "scene.json",
        tmp_path / "input",
        tmp_path / "jobs",
        tmp_path / "blender.exe",
        write_spec(tmp_path, spec),
    )


def test_checked_in_schema_matches_contract_version() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2] / "pipeline" / "scene-render-batch.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == CONTRACT_SCHEMA_VERSION


def test_happy_path_publishes_contract_manifest_and_links_both_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = install_fakes(monkeypatch)

    result = run_pipeline(tmp_path)

    assert result.status == "accepted"
    assert result.scene_fingerprint == FINGERPRINT
    assert result.batch_id == BATCH_ID
    assert result.graphics_status == "accepted"
    assert result.model_calls == 0
    assert recorded["compose"] == 1
    assert recorded["batch"] == 1
    assert recorded["items"] is not None
    asset_roots = {str(item["asset_root"]) for item in recorded["items"]}
    assert asset_roots == {str(tmp_path / "jobs" / FINGERPRINT)}
    assert [item["assets"]["product_image"] for item in recorded["items"]] == ["render.png", "render.png"]
    canvases = {str(item["request_id"]): item["canvas"] for item in recorded["items"]}
    assert canvases == {
        f"sc{FINGERPRINT[:12]}-square": {"width": 1080, "height": 1080},
        f"sc{FINGERPRINT[:12]}-story": {"width": 1080, "height": 1920},
    }

    pipeline_dir = tmp_path / "jobs" / PIPELINE_DIRECTORY / result.pipeline_id
    contract_text = (pipeline_dir / CONTRACT_FILENAME).read_text(encoding="utf-8")
    assert "C:" not in contract_text and "\\" not in contract_text
    contract = parse_scene_render_batch(json.loads(contract_text))
    assert contract.schema_version == CONTRACT_SCHEMA_VERSION
    assert contract.scene.artifact_dir == FINGERPRINT
    assert contract.scene.render_png.path == "render.png"
    assert contract.scene.render_png.width == 1024
    assert contract.graphics.template_id == "product_card"
    assert contract.graphics.variants == ("square", "story")

    manifest = json.loads((pipeline_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "scene-to-graphics.pipeline.v1"
    assert manifest["pipeline_id"] == result.pipeline_id
    assert manifest["scene_stage"]["fingerprint"] == FINGERPRINT
    assert manifest["graphics_stage"]["batch_id"] == BATCH_ID
    assert manifest["model_calls"] == 0
    artifacts = [
        artifact for item in manifest["graphics_stage"]["items"] for artifact in artifact_list(item)
    ]
    assert len(artifacts) == 4
    for artifact in artifacts:
        assert (tmp_path / "jobs" / artifact["path"]).is_file()
        assert "\\" not in artifact["path"]


def artifact_list(item: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = item.get("artifacts", [])
    assert isinstance(artifacts, list)
    return [entry for entry in artifacts if isinstance(entry, dict)]


def test_replay_verifies_records_without_rerunning_the_graphics_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    first = run_pipeline(tmp_path)
    assert first.status == "accepted"

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("graphics stage must not run on pipeline replay")

    monkeypatch.setattr(pipeline_jobs, "run_batch", forbidden)
    manifest_path = (
        tmp_path / "jobs" / PIPELINE_DIRECTORY / first.pipeline_id / MANIFEST_FILENAME
    )
    manifest_before = manifest_path.read_bytes()

    second = run_pipeline(tmp_path)

    assert second.status == "replayed"
    assert second.pipeline_id == first.pipeline_id
    assert second.scene_fingerprint == FINGERPRINT
    assert second.batch_id == BATCH_ID
    assert manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize(
    "overrides",
    [
        {"template_id": "unknown"},
        {"variants": []},
        {"variants": ["square", "square"]},
        {"variants": ["square", "wide"]},
        {"schema_version": "scene-graphics-spec.v2"},
        {"brand": {"primary": "blue", "accent": "#FCA311"}},
        {"content": {"title": "   "}},
    ],
)
def test_invalid_spec_is_rejected_before_any_stage_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    recorded = install_fakes(monkeypatch)

    with pytest.raises(PipelineError) as error:
        run_pipeline(tmp_path, spec=spec_value(**overrides))

    assert error.value.code == "invalid_graphics_spec"
    assert recorded["compose"] == 0
    assert recorded["batch"] == 0


def test_read_graphics_spec_rejects_oversized_input(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_text(json.dumps(spec_value()) + " " * (70 * 1024), encoding="utf-8")

    with pytest.raises(PipelineError) as error:
        read_graphics_spec(big)

    assert error.value.code == "invalid_graphics_spec"


def test_scene_stage_failure_propagates_its_stable_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_compose(*args: object, **kwargs: object) -> JobResult:
        raise SceneInputError("blender_unavailable")

    monkeypatch.setattr(pipeline_jobs, "compose_scene", failing_compose)

    with pytest.raises(SceneInputError) as error:
        run_pipeline(tmp_path)

    assert error.value.code == "blender_unavailable"


def test_tampered_scene_render_fails_contract_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    original_compose = pipeline_jobs.compose_scene

    def tampering_compose(*args: object, **kwargs: object) -> JobResult:
        result = original_compose(*args, **kwargs)  # type: ignore[arg-type]
        (result.artifact_dir / "render.png").write_bytes(b"tampered")
        return result

    monkeypatch.setattr(pipeline_jobs, "compose_scene", tampering_compose)

    with pytest.raises(PipelineError) as error:
        run_pipeline(tmp_path)

    assert error.value.code == "contract_validation_failed"


def test_partial_graphics_batch_fails_without_publishing_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch, batch_status="partial")

    with pytest.raises(PipelineError) as error:
        run_pipeline(tmp_path)

    assert error.value.code == "graphics_batch_failed"
    pipeline_root = tmp_path / "jobs" / PIPELINE_DIRECTORY
    published = list(pipeline_root.iterdir()) if pipeline_root.is_dir() else []
    assert all(not (job / MANIFEST_FILENAME).exists() for job in published)


def test_modified_published_contract_is_conflicting_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    first = run_pipeline(tmp_path)
    contract_path = (
        tmp_path / "jobs" / PIPELINE_DIRECTORY / first.pipeline_id / CONTRACT_FILENAME
    )
    contract_path.write_text('{"schema_version": "scene-render-batch.v1"}', encoding="utf-8")

    with pytest.raises(PipelineError) as error:
        run_pipeline(tmp_path)

    assert error.value.code == "pipeline_conflict"


def test_corrupt_pipeline_manifest_is_conflicting_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    first = run_pipeline(tmp_path)
    manifest_path = (
        tmp_path / "jobs" / PIPELINE_DIRECTORY / first.pipeline_id / MANIFEST_FILENAME
    )
    manifest_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PipelineError) as error:
        run_pipeline(tmp_path)

    assert error.value.code == "pipeline_conflict"


def test_invalid_timeout_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as error:
        run_scene_to_graphics(
            tmp_path / "scene.json",
            tmp_path / "input",
            tmp_path / "jobs",
            tmp_path / "blender.exe",
            write_spec(tmp_path),
            timeout_s=0,
        )

    assert error.value.code == "invalid_timeout"


def test_pipeline_cli_maps_result_rejections_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    success = PipelineResult(
        "accepted", "p" * 64, tmp_path, "accepted", "a" * 64, BATCH_ID, "accepted"
    )
    monkeypatch.setattr(pipeline_cli, "run_scene_to_graphics", lambda *args, **kwargs: success)
    argv = [
        "scene-to-graphics",
        "--scene", "scene.json",
        "--input-root", "input",
        "--spec", "spec.json",
        "--output-root", "jobs",
        "--blender", "blender.exe",
    ]

    assert pipeline_cli.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "accepted"
    assert payload["pipeline_id"] == "p" * 64
    assert payload["model_calls"] == 0

    def rejected(*args: object, **kwargs: object) -> PipelineResult:
        raise PipelineError("invalid_graphics_spec")

    monkeypatch.setattr(pipeline_cli, "run_scene_to_graphics", rejected)
    assert pipeline_cli.main(argv) == 2
    assert json.loads(capsys.readouterr().err) == {"status": "rejected", "error": "invalid_graphics_spec"}


def test_platform_cli_routes_the_pipeline_family(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[list[str]] = []

    def fake_pipeline_main(argv: list[str]) -> int:
        received.append(argv)
        return 5

    monkeypatch.setattr(platform_cli, "pipeline_main", fake_pipeline_main)

    exit_code = platform_cli.main(["pipeline", "scene-to-graphics", "--scene", "x"])

    assert exit_code == 5
    assert received == [["scene-to-graphics", "--scene", "x"]]
