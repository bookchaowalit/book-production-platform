"""Adapter tests on small synthetic manifests shaped like each tool's real output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from production_hub.cli import main
from production_hub.hub import Hub
from production_hub.ingest import IngestError, detect_format, ingest_manifest


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _manifest(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _ingest(hub: Hub, manifest: Path, **kwargs: Any) -> Any:
    with hub.transaction() as conn:
        return ingest_manifest(hub, conn, manifest, **kwargs)


def _graphics(job: Path, *, rejected: bool = False, tamper: bool = False) -> Path:
    svg = _write(job / "q1.svg", b"<svg>q1</svg>")
    png = _write(job / "q1.png", b"\x89PNG q1")
    items: list[dict[str, Any]] = [{
        "request_id": "q1", "status": "accepted",
        "artifacts": [
            {"filename": "q1.svg", "media_type": "image/svg+xml", "content_hash": svg, "byte_size": 13,
             "width": 1080, "height": 1080},
            {"filename": "q1.png", "media_type": "image/png", "content_hash": "0" * 64 if tamper else png,
             "byte_size": 7, "width": 1080, "height": 1080},
        ],
    }]
    if rejected:
        items.append({"request_id": "q2", "status": "rejected", "artifacts": [], "error_code": "overflow"})
    return _manifest(job / "manifest-abc.json", {
        "schema_version": "graphics-batch.v1", "batch_id": "b1", "status": "accepted",
        "metadata": {"contract_version": "graphics.v1a", "recipe_version": "template-batch-v2"}, "items": items,
    })


def test_graphics_batch_registers_outputs_and_skips_rejected(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    result = _ingest(hub, _graphics(tmp_path / "g", rejected=True), project_id="pod-1")
    assert [item.kind for item in result.outputs] == ["graphic.svg", "graphic.png"]
    assert result.skipped == ["q2: rejected"]
    with hub.db.connect() as conn:
        record = hub.db.get(conn, result.outputs[1].artifact_id)
    assert record is not None
    assert record["producer"] == {"tool_id": "graphics-v1a", "tool_version": "graphics.v1a", "job_id": "b1",
                                  "recipe_version": "template-batch-v2"}
    assert record["attributes"]["qa_manifest_id"] == result.manifest.artifact_id
    assert record["attributes"]["width"] == 1080
    assert record["project_id"] == "pod-1"


def test_hash_mismatch_rejects_whole_manifest_without_leftovers(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with pytest.raises(IngestError, match="sha256 differs"):
        _ingest(hub, _graphics(tmp_path / "g", tamper=True))
    with hub.db.connect() as conn:
        assert hub.db.list(conn) == []
    objects = tmp_path / "home" / "store" / "sha256"
    assert not objects.exists() or not any(path.is_file() for path in objects.rglob("*"))


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    manifest = _graphics(tmp_path / "g")
    _ingest(hub, manifest)
    again = _ingest(hub, manifest)
    assert not any(item.created for item in again.outputs)
    with hub.db.connect() as conn:
        assert len(hub.db.list(conn)) == 3


def test_scene3d_links_registered_source_and_records_unregistered(tmp_path: Path) -> None:
    job = tmp_path / "scene" / "fp1"
    glb = b"glTF model"
    render = _write(job / "render.png", b"\x89PNG render")
    blend = _write(job / "scene.blend", b"BLENDER")
    manifest = _manifest(job / "qa-manifest.json", {
        "schema_version": "scene3d.qa.v1", "status": "accepted", "fingerprint": "fp1",
        "composer_version": "scene-composer.v1a.2", "recipe": "product-hero.studio.v1",
        "source_asset_sha256": hashlib.sha256(glb).hexdigest(),
        "outputs": {"render.png": {"sha256": render, "bytes": 12, "width": 1024, "height": 1024},
                    "scene.blend": {"sha256": blend, "bytes": 7}},
    })
    hub = Hub(tmp_path / "home")
    unlinked = _ingest(hub, manifest)
    with hub.db.connect() as conn:
        record = hub.db.get(conn, unlinked.outputs[0].artifact_id)
    assert record is not None and record["lineage"] == []
    assert record["attributes"]["unregistered_inputs"] == ["sha256:" + hashlib.sha256(glb).hexdigest()]

    hub2 = Hub(tmp_path / "home2")
    source = tmp_path / "in" / "product.glb"
    _write(source, glb)
    linked = _ingest(hub2, manifest, input_files=[source])
    with hub2.db.connect() as conn:
        record = hub2.db.get(conn, linked.outputs[0].artifact_id)
    assert record is not None
    assert record["kind"] == "image.render"
    assert record["lineage"] == [linked.inputs[0].artifact_id]
    assert "unregistered_inputs" not in record["attributes"]


def test_scene_to_graphics_links_graphics_to_render(tmp_path: Path) -> None:
    root = tmp_path / "scene-root"
    render = _write(root / "fp1" / "render.png", b"\x89PNG render")
    square = _write(root / ".pipeline" / "p1" / "graphics" / "sq.png", b"\x89PNG square")
    manifest = _manifest(root / ".pipeline" / "p1" / "pipeline-manifest.json", {
        "schema_version": "scene-to-graphics.pipeline.v1", "pipeline_id": "p1", "status": "accepted",
        "recipe": "scene-to-graphics.v1",
        "scene_stage": {"fingerprint": "fp1", "artifact_dir": "fp1",
                        "render_png": {"path": "render.png", "sha256": render, "width": 1024, "height": 1024}},
        "graphics_stage": {"batch_id": "gb1", "items": [{"request_id": "sq", "status": "accepted", "artifacts": [
            {"path": ".pipeline/p1/graphics/sq.png", "media_type": "image/png", "sha256": square}]}]},
    })
    hub = Hub(tmp_path / "home")
    result = _ingest(hub, manifest)
    render_id, square_id = (item.artifact_id for item in result.outputs)
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, square_id) == [render_id]


def test_spreadsheet_data_and_documents(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    xlsx = _write(tmp_path / "s" / "workbook.xlsx", b"PK xlsx")
    sheet = _ingest(hub, _manifest(tmp_path / "s" / "manifest.json", {
        "schema_version": "spreadsheet-job.v1", "job_id": "s1", "status": "accepted",
        "metadata": {"recipe_version": "workbook-builder-v1"},
        "artifact": {"filename": "workbook.xlsx", "content_hash": xlsx, "byte_size": 7,
                     "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    }))
    assert [item.kind for item in sheet.outputs] == ["workbook.xlsx"]

    csv = _write(tmp_path / "d" / "cleaned.csv", b"a,b\n1,2\n")
    chart = _write(tmp_path / "d" / "chart.svg", b"<svg>c</svg>")
    data = _ingest(hub, _manifest(tmp_path / "d" / "manifest.json", {
        "schema_version": "data-job.v1", "job_id": "d1", "status": "accepted",
        "metadata": {"recipe_version": "csv-cleaner-chart-builder-v2"},
        "artifacts": [
            {"filename": "cleaned.csv", "media_type": "text/csv; charset=utf-8", "content_hash": csv},
            {"filename": "chart.svg", "media_type": "image/svg+xml", "content_hash": chart},
        ],
    }))
    assert [item.kind for item in data.outputs] == ["dataset.table", "chart.svg"]

    pdf = _write(tmp_path / "doc" / "document.pdf", b"%PDF-1.7")
    docs_manifest = {
        "job_id": "doc-1", "recipe": "invoice-v1", "request_hash": "f" * 64, "status": "accepted",
        "qa": {"status": "passed", "issues": [], "page_count": 1},
        "artifacts": [{"filename": "document.pdf", "media_type": "application/pdf", "content_hash": pdf,
                       "page_count": 1}],
    }
    assert detect_format(docs_manifest) == "document-production-manifest"
    docs = _ingest(hub, _manifest(tmp_path / "doc" / "manifest.json", docs_manifest))
    assert docs.outputs[0].kind == "document.pdf"

    failing = _manifest(tmp_path / "doc2" / "manifest.json", {**docs_manifest, "qa": {"status": "failed"}})
    with pytest.raises(IngestError, match="QA did not pass"):
        _ingest(hub, failing)


def test_pipeline_report_chains_stage_lineage(tmp_path: Path) -> None:
    run = tmp_path / "run"
    csv = _write(run / "stages/01/cleaned.csv", b"m,v\n1,2\n")
    chart = _write(run / "stages/02/chart.svg", b"<svg>chart</svg>")
    xlsx = _write(run / "stages/03/workbook.xlsx", b"PK wb")
    pdf = _write(run / "stages/04/document.pdf", b"%PDF report")
    _manifest(run / "stages/04/request.json", {"charts": [{"content_hash": chart}], "workbook": {"content_hash": xlsx}})

    def stage(name: str, job: str, artifacts: list[tuple[str, str, str]], **extra: Any) -> dict[str, Any]:
        return {"stage": name, "status": "accepted", "job_id": job, "output_contract": "c.v1", **extra,
                "artifacts": [{"name": n, "path": p, "content_hash": h} for n, p, h in artifacts]}

    manifest = _manifest(run / "pipeline-manifest.json", {
        "schema_version": "pipeline-report.v1", "pipeline_id": "monthly-1", "stages": [
            stage("data_pipeline", "j1", [("cleaned.csv", "stages/01/cleaned.csv", csv)]),
            stage("data_viz", "j2", [("chart.svg", "stages/02/chart.svg", chart)]),
            stage("spreadsheet", "j3", [("workbook.xlsx", "stages/03/workbook.xlsx", xlsx)]),
            stage("documents", "j4", [("document.pdf", "stages/04/document.pdf", pdf)],
                  request_path="stages/04/request.json"),
        ],
    })
    hub = Hub(tmp_path / "home")
    result = _ingest(hub, manifest)
    ids = {item.filename: item.artifact_id for item in result.outputs}
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, ids["chart.svg"]) == [ids["cleaned.csv"]]
        assert hub.db.inputs(conn, ids["workbook.xlsx"]) == [ids["chart.svg"]]
        assert sorted(hub.db.inputs(conn, ids["document.pdf"])) == sorted([ids["chart.svg"], ids["workbook.xlsx"]])
        tree = hub.lineage_tree(conn, ids["document.pdf"])
    depth = 0
    node = tree
    while node.get("inputs"):
        node = node["inputs"][0]
        depth += 1
    assert depth >= 2


def test_audio_batch_and_master(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    source = tmp_path / "raw" / "voice.wav"
    source_sha = _write(source, b"RIFF raw voice")
    flac = _write(tmp_path / "batch" / "artifacts" / "voice.flac", b"fLaC voice")
    batch = _ingest(hub, _manifest(tmp_path / "batch" / "result.json", {
        "schema_version": "audio-batch-result.v1", "batch_id": "ab1", "status": "completed_with_errors",
        "recipe_version": "audio-v1c", "tracks": [
            {"track_id": "voice", "status": "accepted", "output_relative_path": "artifacts/voice.flac",
             "output_sha256": flac, "output_format": "flac", "source_sha256": source_sha},
            {"track_id": "hot", "status": "rejected", "output_relative_path": None},
        ],
    }), input_files=[source])
    assert batch.skipped == ["hot: rejected"]
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, batch.outputs[0].artifact_id) == ["sha256:" + source_sha]

    master = _write(tmp_path / "master" / "master.wav", b"RIFF master")
    result = _ingest(hub, _manifest(tmp_path / "master" / "qa-manifest.json", {
        "schema_version": "audio-master.qa.v1", "status": "accepted", "fingerprint": "fm1", "audio_id": "ep1",
        "recipe_version": "audio-master.v1a.1", "preset": {"name": "podcast-mono.v1"},
        "qa": {"passed": True}, "source_sha256": flac,
        "output": {"filename": "master.wav", "sha256": master, "media_type": "audio/wav; codec=pcm_s24le"},
    }))
    assert result.outputs[0].kind == "audio.master"
    with hub.db.connect() as conn:
        assert hub.db.inputs(conn, result.outputs[0].artifact_id) == ["sha256:" + flac]


def test_rejects_unknown_formats_escaping_paths_and_missing_outputs(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with pytest.raises(IngestError, match="unsupported manifest format"):
        _ingest(hub, _manifest(tmp_path / "x.json", {"schema_version": "mystery.v9"}))
    escaping = {"schema_version": "spreadsheet-job.v1", "job_id": "s", "status": "accepted",
                "artifact": {"filename": "../secret.xlsx", "content_hash": "a" * 64}}
    with pytest.raises(IngestError, match="escapes"):
        _ingest(hub, _manifest(tmp_path / "e" / "manifest.json", escaping))
    missing = {**escaping, "artifact": {"filename": "workbook.xlsx", "content_hash": "a" * 64}}
    with pytest.raises(IngestError, match="missing"):
        _ingest(hub, _manifest(tmp_path / "m" / "manifest.json", missing))
    with pytest.raises(IngestError, match="missing field"):
        _ingest(hub, _manifest(tmp_path / "f" / "manifest.json", {"schema_version": "spreadsheet-job.v1"}))


def test_cli_ingest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "home")]
    assert main([*home, "ingest", str(_graphics(tmp_path / "g")), "--project", "pod-1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["format"] == "graphics-batch.v1"
    assert len(output["outputs"]) == 2
    assert main([*home, "ingest", str(_manifest(tmp_path / "x.json", {"schema_version": "nope"}))]) == 2
    assert "ingest_error" in capsys.readouterr().err


def test_graphics_export_links_to_source_svg(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    batch_dir = tmp_path / "g"
    _ingest(hub, _graphics(batch_dir))
    svg_hash = hashlib.sha256(b"<svg>q1</svg>").hexdigest()
    out = tmp_path / "export"
    png = _write(out / "q1@3x.png", b"\x89PNG big")
    pdf = _write(out / "q1-print.pdf", b"%PDF print")
    manifest = _manifest(out / "export-x.json", {
        "schema_version": "graphics-export.v1", "export_id": "b1-x3-300dpi", "status": "accepted",
        "recipe": "graphics-print-export.v1", "items": [{
            "request_id": "q1", "source_svg": {"filename": "q1.svg", "content_hash": svg_hash},
            "artifacts": [
                {"filename": "q1@3x.png", "media_type": "image/png", "content_hash": png, "width": 3240, "height": 3240, "dpi": 300},
                {"filename": "q1-print.pdf", "media_type": "application/pdf", "content_hash": pdf, "dpi": 300,
                 "print_size_mm": [274.3, 274.3]},
            ],
        }],
    })
    result = _ingest(hub, manifest)
    assert [item.kind for item in result.outputs] == ["graphic.png", "graphic.pdf"]
    with hub.db.connect() as conn:
        record = hub.db.get(conn, result.outputs[1].artifact_id)
    assert record is not None
    assert record["lineage"] == ["sha256:" + svg_hash]
    assert record["attributes"]["print_size_mm"] == [274.3, 274.3]


def test_cad_manifest_registers_every_format(tmp_path: Path) -> None:
    job = tmp_path / "cad" / "fp"
    files = {"part.FCStd": b"fcstd", "part.step": b"step", "part.stl": b"stl", "preview.png": b"\x89PNG"}
    artifacts = {name: {"sha256": _write(job / name, data)} for name, data in files.items()}
    manifest = _manifest(job / "qa-manifest.json", {
        "schema_version": "cad-qa-manifest.v1", "build_status": "accepted", "fingerprint": "fp",
        "recipe_version": "cad-parametric-mounting-plate-v1a", "template": "mounting-plate.v1",
        "part_ir_sha256": "a" * 64, "artifacts": artifacts,
        "qa": {"bounds_mm": [0, 137, 0, 83, 0, 7.5], "volume_mm3": 83774.5, "bore_count": 4},
    })
    hub = Hub(tmp_path / "home")
    result = _ingest(hub, manifest)
    assert sorted(item.kind for item in result.outputs) == ["cad.fcstd", "cad.step", "cad.stl", "image.render"]
    with hub.db.connect() as conn:
        record = hub.db.get(conn, result.outputs[0].artifact_id)
    assert record is not None and record["attributes"]["bore_count"] == 4
    failed = _manifest(tmp_path / "bad" / "qa-manifest.json", {"schema_version": "cad-qa-manifest.v1", "build_status": "rejected"})
    with pytest.raises(IngestError, match="not accepted"):
        _ingest(hub, failed)


def test_game_manifest_registers_a_deterministic_project_zip(tmp_path: Path) -> None:
    import zipfile

    build = tmp_path / "out" / ("f" * 64)
    audio = build / "project" / "Assets" / "BookProduction" / "Audio" / "music.wav"
    audio.parent.mkdir(parents=True)
    audio_sha = _write(audio, b"RIFF music")
    _write(build / "project" / "Assets" / "Scenes" / "room.unity", b"scene")
    manifest = _manifest(build / "qa-manifest.json", {
        "schema_version": "game-production.qa.v1", "schema_version_game_ir": "game-ir.v2", "status": "accepted",
        "fingerprint": "f" * 64, "composer_version": "game-scene-composer.v1a.2", "unity_version": "6000.6.2f1",
        "outputs": {"project": {"path": "project"}, "scene": {"path": "project/Assets/Scenes/room.unity"}},
        "assets": [{"asset_id": "music", "sha256": audio_sha}],
        "qa": {"gameplay_events": ["player_moved", "game_won:gate"]},
    })
    hub = Hub(tmp_path / "home")
    first = _ingest(hub, manifest)
    [project] = first.outputs
    with hub.db.connect() as conn:
        row = hub.db.get(conn, project.artifact_id)
    assert row is not None
    assert row["kind"] == "game.unity-project"
    assert row["attributes"]["scene_id"] == "room" and row["attributes"]["audio_assets"] == ["music"]
    with zipfile.ZipFile(hub.store.path_for(project.artifact_id)) as archive:
        assert sorted(archive.namelist()) == ["Assets/BookProduction/Audio/music.wav", "Assets/Scenes/room.unity"]
    again = _ingest(Hub(tmp_path / "home2"), manifest)
    assert again.outputs[0].artifact_id == project.artifact_id
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('"accepted"', '"rejected"'), encoding="utf-8")
    with pytest.raises(IngestError, match="QA did not pass"):
        _ingest(Hub(tmp_path / "home3"), manifest)
