"""Ingest adapters: read a tool's own job manifest and register its outputs.

Each adapter turns one manifest into ``Planned`` items. ``ingest_manifest``
then registers, in one transaction: optional explicit input files (as
``external`` artifacts), the manifest itself (``qa.manifest``), and every
accepted output with lineage. The Hub re-hashes every file and refuses the
whole manifest when a hash differs from what the tool recorded.

Lineage comes from input hashes the tool recorded (only when those inputs are
already registered) plus any explicit ``--input-file``. Recorded input hashes
that are not registered are kept in ``attributes.unregistered_inputs`` so the
gap stays visible. Tools without a manifest (Video Studio subtitles and audio
extraction) are registered with ``import-file`` instead.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .hub import ArtifactInput, Hub, Producer, Registered
from .store import file_sha256


class IngestError(ValueError):
    """Raised when a manifest cannot be ingested."""


@dataclass(frozen=True)
class Planned:
    path: Path
    kind: str
    producer: Producer
    expected_sha256: str | None = None
    media_type: str | None = None
    input_hashes: Sequence[str] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    content: bytes | None = None


@dataclass
class IngestResult:
    adapter: str
    manifest: Registered
    inputs: list[Registered]
    outputs: list[Registered]
    skipped: list[str]


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"cannot read manifest {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise IngestError(f"manifest {path.name} is not a JSON object")
    return data


def _child(base: Path, relative: str) -> Path:
    rel = PurePosixPath(relative.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or ":" in relative:
        raise IngestError(f"manifest path escapes its folder: {relative!r}")
    return base.joinpath(*rel.parts)


def _media(value: Any) -> str | None:
    return str(value).split(";")[0].strip() if value else None


def _sha(value: Any) -> str | None:
    return f"sha256:{value}" if isinstance(value, str) and len(value) == 64 else None


_BY_SUFFIX_GRAPHICS = {".svg": "graphic.svg", ".png": "graphic.png", ".jpg": "graphic.png", ".jpeg": "graphic.png"}
_BY_SUFFIX_DATA = {".csv": "dataset.table", ".svg": "chart.svg", ".png": "chart.png", ".json": "qa.manifest"}
_BY_SUFFIX_DOCS = {".docx": "document.docx", ".pdf": "document.pdf"}


def _kind(filename: str, table: dict[str, str]) -> str:
    kind = table.get(PurePosixPath(filename).suffix.lower())
    if kind is None:
        raise IngestError(f"no artifact kind for output {filename!r}")
    return kind


def _graphics_items(
    manifest: dict[str, Any], base: Path, producer: Producer, path_key: str, input_hashes: Sequence[str] = ()
) -> tuple[list[Planned], list[str]]:
    planned: list[Planned] = []
    skipped: list[str] = []
    for item in manifest.get("items", []):
        if item.get("status") != "accepted":
            skipped.append(f"{item.get('request_id')}: {item.get('status')}")
            continue
        for artifact in item.get("artifacts", []):
            name = artifact[path_key]
            planned.append(Planned(
                path=_child(base, name),
                kind=_kind(name, _BY_SUFFIX_GRAPHICS),
                producer=producer,
                expected_sha256=_sha(artifact.get("content_hash") or artifact.get("sha256")),
                media_type=_media(artifact.get("media_type")),
                input_hashes=input_hashes,
                attributes={
                    key: artifact[key] for key in ("width", "height") if key in artifact
                } | {"request_id": item.get("request_id")},
            ))
    return planned, skipped


def _adapt_graphics(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    meta = manifest.get("metadata", {})
    producer = Producer(
        "graphics-v1a", meta.get("contract_version", "graphics.v1a"), manifest["batch_id"], meta.get("recipe_version")
    )
    return _graphics_items(manifest, path.parent, producer, "filename")


def _adapt_scene_to_graphics(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    root = path.parent.parent.parent
    scene = manifest["scene_stage"]
    render = scene["render_png"]
    render_sha = _sha(render["sha256"])
    scene_item = Planned(
        path=_child(root, f"{scene['artifact_dir']}/{render['path']}"),
        kind="image.render",
        producer=Producer("scene3d-v1a", "scene3d", scene["fingerprint"]),
        expected_sha256=render_sha,
        media_type="image/png",
        attributes={"width": render.get("width"), "height": render.get("height")},
    )
    stage = manifest["graphics_stage"]
    producer = Producer("graphics-v1a", "graphics.v1a", stage["batch_id"], manifest.get("recipe"))
    items, skipped = _graphics_items(stage, root, producer, "path")
    linked = [replace(item, input_hashes=[render_sha] if render_sha else []) for item in items]
    return [scene_item, *linked], skipped


def _adapt_scene3d(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    producer = Producer("scene3d-v1a", manifest["composer_version"], manifest["fingerprint"], manifest.get("recipe"))
    inputs = [value for value in (_sha(manifest.get("source_asset_sha256")),) if value]
    planned = []
    for name, info in manifest["outputs"].items():
        kind = "image.render" if name.endswith(".png") else "scene3d.blend"
        planned.append(Planned(
            path=_child(path.parent, name), kind=kind, producer=producer,
            expected_sha256=_sha(info.get("sha256")), input_hashes=inputs,
            attributes={key: info[key] for key in ("width", "height") if key in info},
        ))
    return planned, []


def _adapt_spreadsheet(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    artifact = manifest["artifact"]
    producer = Producer(
        "spreadsheet-v1a", manifest["schema_version"], manifest["job_id"],
        manifest.get("metadata", {}).get("recipe_version"),
    )
    return [Planned(
        path=_child(path.parent, artifact["filename"]), kind="workbook.xlsx", producer=producer,
        expected_sha256=_sha(artifact["content_hash"]), media_type=_media(artifact.get("media_type")),
    )], []


def _adapt_data(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    producer = Producer(
        "data-viz-v1a", manifest["schema_version"], manifest["job_id"],
        manifest.get("metadata", {}).get("recipe_version"),
    )
    return [
        Planned(
            path=_child(path.parent, artifact["filename"]), kind=_kind(artifact["filename"], _BY_SUFFIX_DATA),
            producer=producer, expected_sha256=_sha(artifact["content_hash"]),
            media_type=_media(artifact.get("media_type")),
        )
        for artifact in manifest["artifacts"]
    ], []


def _adapt_documents(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("qa", {}).get("status") != "passed":
        raise IngestError("document job QA did not pass")
    producer = Producer("documents-v1a", "document-engine.v1", manifest["job_id"], manifest.get("recipe"))
    return [
        Planned(
            path=_child(path.parent, artifact["filename"]), kind=_kind(artifact["filename"], _BY_SUFFIX_DOCS),
            producer=producer, expected_sha256=_sha(artifact["content_hash"]),
            media_type=_media(artifact.get("media_type")),
            attributes={"page_count": artifact["page_count"]} if artifact.get("page_count") else {},
        )
        for artifact in manifest["artifacts"]
    ], []


_STAGE_TOOLS = {
    "data_pipeline": ("data-viz-v1a", _BY_SUFFIX_DATA),
    "data_viz": ("data-viz-v1a", _BY_SUFFIX_DATA),
    "spreadsheet": ("spreadsheet-v1a", {".xlsx": "workbook.xlsx", ".json": "qa.manifest"}),
    "documents": ("documents-v1a", _BY_SUFFIX_DOCS),
}


def _adapt_pipeline_report(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    base = path.parent
    planned: list[Planned] = []
    previous: list[str] = []
    for stage in manifest["stages"]:
        if stage.get("status") != "accepted":
            raise IngestError(f"pipeline stage {stage.get('stage')} is not accepted")
        tool_id, table = _STAGE_TOOLS[stage["stage"]]
        producer = Producer(tool_id, stage.get("output_contract", "unknown"), stage["job_id"], manifest["pipeline_id"])
        stage_inputs = previous
        if stage["stage"] == "documents" and stage.get("request_path"):
            request = _load(_child(base, stage["request_path"]))
            hashes = [chart.get("content_hash") for chart in request.get("charts", [])]
            hashes.append((request.get("workbook") or {}).get("content_hash"))
            stage_inputs = [value for value in (_sha(item) for item in hashes) if value] or previous
        current: list[str] = []
        for artifact in stage["artifacts"]:
            sha = _sha(artifact["content_hash"])
            planned.append(Planned(
                path=_child(base, artifact["path"]), kind=_kind(artifact["name"], table), producer=producer,
                expected_sha256=sha, media_type=_media(artifact.get("media_type")),
                input_hashes=list(stage_inputs), attributes={"pipeline_stage": stage["stage"]},
            ))
            if sha and not artifact["name"].endswith(".json"):
                current.append(sha)
        previous = current
    return planned, []


def _adapt_audio_batch(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    producer = Producer("audio-batch-v1a", manifest["recipe_version"], manifest["batch_id"], manifest["recipe_version"])
    planned: list[Planned] = []
    skipped: list[str] = []
    for track in manifest["tracks"]:
        if track.get("status") != "accepted" or not track.get("output_relative_path"):
            skipped.append(f"{track.get('track_id')}: {track.get('status')}")
            continue
        media = "audio/flac" if track.get("output_format") == "flac" else "audio/wav"
        planned.append(Planned(
            path=_child(path.parent, track["output_relative_path"]), kind="audio.track", producer=producer,
            expected_sha256=_sha(track.get("output_sha256")), media_type=media,
            input_hashes=[value for value in (_sha(track.get("source_sha256")),) if value],
            attributes={"track_id": track.get("track_id")},
        ))
    return planned, skipped


def _adapt_audio_master(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if not manifest.get("qa", {}).get("passed"):
        raise IngestError("audio master QA did not pass")
    output = manifest["output"]
    producer = Producer(
        "audio-master-v1a", manifest["recipe_version"], manifest["fingerprint"], manifest.get("preset", {}).get("name")
    )
    return [Planned(
        path=_child(path.parent, output["filename"]), kind="audio.master", producer=producer,
        expected_sha256=_sha(output.get("sha256")), media_type=_media(output.get("media_type")),
        input_hashes=[value for value in (_sha(manifest.get("source_sha256")),) if value],
        attributes={"audio_id": manifest.get("audio_id")},
    )], []


def _adapt_campaign(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    package = manifest["package"]
    meta = manifest.get("metadata", {})
    producer = Producer(
        "marketing-v1a", meta.get("contract_version", "marketing.v1a"),
        str(manifest.get("idempotency_key") or manifest["campaign_id"]), meta.get("recipe_version"),
    )
    package_path = _child(path.parent, package["filename"])
    package_sha = _sha(package["content_hash"])
    planned = [Planned(
        path=package_path, kind="campaign.package", producer=producer, expected_sha256=package_sha,
        media_type="application/json", attributes={"campaign_id": manifest["campaign_id"]},
    )]
    try:
        campaign = json.loads(package_path.read_text(encoding="utf-8"))["campaign"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise IngestError(f"cannot read campaign package: {exc}") from exc
    asset_hashes = manifest.get("lineage", {}).get("asset_hashes", {})
    for asset in campaign.get("assets", []):
        if asset.get("kind") not in ("copy", "email") or not asset.get("text"):
            continue
        name = f"{asset['asset_id']}-{asset.get('channel', 'any')}.txt"
        planned.append(Planned(
            path=Path(name), kind="copy.text", producer=producer,
            expected_sha256=_sha(asset_hashes.get(asset["asset_id"])), media_type="text/plain",
            input_hashes=[package_sha] if package_sha else [],
            attributes={"campaign_id": manifest["campaign_id"], "channel": asset.get("channel"),
                        "asset_id": asset["asset_id"]},
            content=str(asset["text"]).encode("utf-8"),
        ))
    return planned, []


def _adapt_video_remux(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if not manifest.get("qa", {}).get("passed"):
        raise IngestError("video remux QA did not pass")
    output = manifest["output"]
    producer = Producer("video-studio", manifest["schema_version"], manifest["remux_id"], manifest.get("recipe"))
    inputs = [
        value for value in (
            _sha(manifest.get("source_video", {}).get("sha256")),
            _sha(manifest.get("source_audio", {}).get("sha256")),
        ) if value
    ]
    return [Planned(
        path=_child(path.parent, output["filename"]), kind="video.render", producer=producer,
        expected_sha256=_sha(output.get("sha256")), media_type=_media(output.get("media_type")),
        input_hashes=inputs, attributes={"duration_seconds": output.get("duration_seconds")},
    )], []


def _adapt_bim_takeoff(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("status") != "complete":
        raise IngestError(f"BIM takeoff status is {manifest.get('status')!r}, not complete")
    producer = Producer(
        "bim-v1a", manifest.get("report_schema_version", manifest["schema_version"]), path.parent.name,
        str(manifest.get("takeoff_id") or ""),
    )
    inputs = [value for value in (_sha(manifest.get("source_sha256")),) if value]
    planned = []
    for name, info in sorted(manifest["artifacts"].items()):
        media = "text/csv" if name.endswith(".csv") else "application/json"
        planned.append(Planned(
            path=_child(path.parent, name), kind="bim.takeoff", producer=producer,
            expected_sha256=_sha(info.get("sha256")), media_type=media, input_hashes=inputs,
            attributes={"ifc_schema": manifest.get("ifc_schema"), "row_count": manifest.get("row_count")},
        ))
    return planned, []


def _adapt_web(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("status") != "accepted":
        raise IngestError("web page job is not accepted")
    meta = manifest.get("metadata", {})
    producer = Producer(
        "web-v1a", meta.get("contract_version", "web.v1a"), str(manifest.get("idempotency_key") or manifest["page_id"]),
        meta.get("recipe_version"),
    )
    return [
        Planned(
            path=_child(path.parent, artifact["filename"]), kind="web.page", producer=producer,
            expected_sha256=_sha(artifact["content_hash"]), media_type=_media(artifact.get("media_type")),
            attributes={"page_id": manifest.get("page_id"), "template_ref": meta.get("template_ref")},
        )
        for artifact in manifest["artifacts"]
    ], []


def _adapt_video_overlay(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if not manifest.get("qa", {}).get("passed"):
        raise IngestError("video overlay QA did not pass")
    output = manifest["output"]
    producer = Producer("video-studio", manifest["schema_version"], manifest["overlay_id"], manifest.get("recipe"))
    inputs = [
        value for value in (
            _sha(manifest.get("source_video", {}).get("sha256")),
            _sha(manifest.get("source_image", {}).get("sha256")),
        ) if value
    ]
    return [Planned(
        path=_child(path.parent, output["filename"]), kind="video.render", producer=producer,
        expected_sha256=_sha(output.get("sha256")), media_type=_media(output.get("media_type")),
        input_hashes=inputs, attributes={"duration_seconds": output.get("duration_seconds"),
                                         "placement": manifest.get("placement")},
    )], []


def _adapt_graphics_export(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("status") != "accepted":
        raise IngestError("graphics export is not accepted")
    producer = Producer("graphics-v1a", manifest["schema_version"], manifest["export_id"], manifest.get("recipe"))
    planned: list[Planned] = []
    for item in manifest["items"]:
        source = _sha(item.get("source_svg", {}).get("content_hash"))
        for artifact in item["artifacts"]:
            kind = "graphic.pdf" if artifact["media_type"] == "application/pdf" else "graphic.png"
            planned.append(Planned(
                path=_child(path.parent, artifact["filename"]), kind=kind, producer=producer,
                expected_sha256=_sha(artifact["content_hash"]), media_type=_media(artifact["media_type"]),
                input_hashes=[source] if source else [],
                attributes={key: artifact[key] for key in ("width", "height", "dpi", "print_size_mm") if key in artifact}
                | {"request_id": item.get("request_id")},
            ))
    return planned, []


_CAD_KINDS = {
    ".fcstd": ("cad.fcstd", "application/x-freecad"), ".step": ("cad.step", "model/step"),
    ".stl": ("cad.stl", "model/stl"), ".png": ("image.render", "image/png"), ".pdf": ("cad.drawing.pdf", "application/pdf"),
}


def _adapt_cad(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("build_status") != "accepted":
        raise IngestError(f"CAD build status is {manifest.get('build_status')!r}, not accepted")
    producer = Producer("cad-v1a", manifest["schema_version"], manifest["fingerprint"], manifest.get("recipe_version"))
    qa = manifest.get("qa", {})
    attributes = {key: qa[key] for key in ("bounds_mm", "volume_mm3", "bore_count", "solid_count") if key in qa}
    attributes["template"] = manifest.get("template")
    inputs = [value for value in (_sha(manifest.get("part_ir_sha256")),) if value]
    planned = []
    for name, info in sorted(manifest["artifacts"].items()):
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in _CAD_KINDS:
            raise IngestError(f"no artifact kind for CAD output {name!r}")
        kind, media = _CAD_KINDS[suffix]
        planned.append(Planned(
            path=_child(path.parent, name), kind=kind, producer=producer, expected_sha256=_sha(info.get("sha256")),
            media_type=media, input_hashes=inputs, attributes=dict(attributes),
        ))
    return planned, []


Adapter = Callable[[dict[str, Any], Path], tuple[list[Planned], list[str]]]

def _zip_tree(root: Path) -> bytes:
    """Deterministic zip of a folder: sorted paths, fixed timestamps and permissions."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(item for item in root.rglob("*") if item.is_file() and not item.is_symlink()):
            info = zipfile.ZipInfo(file.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, file.read_bytes())
    return buffer.getvalue()


def _adapt_game(manifest: dict[str, Any], path: Path) -> tuple[list[Planned], list[str]]:
    if manifest.get("status") != "accepted":
        raise IngestError("game build QA did not pass")
    project = _child(path.parent, str(manifest["outputs"]["project"]["path"]))
    if not project.is_dir():
        raise IngestError("game build has no project folder")
    scene_path = str(manifest["outputs"]["scene"]["path"])
    scene_id = Path(scene_path).stem
    producer = Producer("game-v1a", manifest["composer_version"], manifest["fingerprint"], manifest["unity_version"])
    assets = manifest.get("assets") or []
    return [Planned(
        path=Path(f"{scene_id}-unity-project.zip"), kind="game.unity-project", producer=producer,
        media_type="application/zip",
        input_hashes=[value for value in (_sha(item.get("sha256")) for item in assets) if value],
        attributes={
            "scene_id": scene_id, "game_ir": manifest.get("schema_version_game_ir"),
            "fingerprint": manifest["fingerprint"], "unity_version": manifest["unity_version"],
            "gameplay_events": len(manifest.get("qa", {}).get("gameplay_events", [])),
            "audio_assets": [item.get("asset_id") for item in assets] or None,
        },
        content=_zip_tree(project),
    )], []


ADAPTERS: dict[str, tuple[str, Adapter]] = {
    "graphics-batch.v1": ("graphics-v1a", _adapt_graphics),
    "scene-to-graphics.pipeline.v1": ("graphics-v1a", _adapt_scene_to_graphics),
    "scene3d.qa.v1": ("scene3d-v1a", _adapt_scene3d),
    "spreadsheet-job.v1": ("spreadsheet-v1a", _adapt_spreadsheet),
    "data-job.v1": ("data-viz-v1a", _adapt_data),
    "document-production-manifest": ("documents-v1a", _adapt_documents),
    "pipeline-report.v1": ("production-pipelines", _adapt_pipeline_report),
    "audio-batch-result.v1": ("audio-batch-v1a", _adapt_audio_batch),
    "audio-master.qa.v1": ("audio-master-v1a", _adapt_audio_master),
    "campaign-manifest.v1": ("marketing-v1a", _adapt_campaign),
    "video-remux.v1": ("video-studio", _adapt_video_remux),
    "bim-takeoff-qa.v1": ("bim-v1a", _adapt_bim_takeoff),
    "web-manifest.v1": ("web-v1a", _adapt_web),
    "video-overlay.v1": ("video-studio", _adapt_video_overlay),
    "graphics-export.v1": ("graphics-v1a", _adapt_graphics_export),
    "cad-qa-manifest.v1": ("cad-v1a", _adapt_cad),
    "game-production.qa.v1": ("game-v1a", _adapt_game),
}


def detect_format(manifest: dict[str, Any]) -> str:
    schema = manifest.get("schema_version")
    if isinstance(schema, str) and schema in ADAPTERS:
        return schema
    if schema is None and {"job_id", "recipe", "artifacts", "request_hash"} <= manifest.keys():
        return "document-production-manifest"
    raise IngestError(f"unsupported manifest format: {schema!r}")


def ingest_manifest(
    hub: Hub,
    conn: sqlite3.Connection,
    manifest_path: Path,
    *,
    input_files: Sequence[Path] = (),
    input_ids: Sequence[str] = (),
    project_id: str | None = None,
    privacy_class: str = "internal",
) -> IngestResult:
    manifest = _load(manifest_path)
    fmt = detect_format(manifest)
    producer_tool, adapter = ADAPTERS[fmt]
    try:
        planned, skipped = adapter(manifest, manifest_path)
    except KeyError as exc:
        raise IngestError(f"{fmt} manifest is missing field {exc}") from exc

    job = str(manifest.get("job_id") or manifest.get("batch_id") or manifest.get("fingerprint")
              or manifest.get("pipeline_id") or manifest_path.parent.name)
    inputs = [
        hub.register(conn, ArtifactInput(
            source, _input_kind(source), Producer("external", "n/a", f"input-for:{job}"), project_id=project_id, privacy_class=privacy_class
        ))
        for source in input_files
    ]
    for artifact_id in input_ids:
        if hub.db.get(conn, artifact_id) is None:
            raise IngestError(f"input artifact is not registered: {artifact_id}")
    explicit = [*input_ids, *(item.artifact_id for item in inputs)]
    manifest_record = hub.register(conn, ArtifactInput(
        manifest_path, "qa.manifest", Producer(producer_tool, fmt, job), lineage=explicit, project_id=project_id, privacy_class=privacy_class,
    ))
    outputs: list[Registered] = []
    derived_dir = hub.home / "derived" / manifest_record.artifact_id[7:23]
    for item in planned:
        if item.content is not None:
            derived_dir.mkdir(parents=True, exist_ok=True)
            target = derived_dir / item.path.name
            target.write_bytes(item.content)
            item = replace(item, path=target)
        if not item.path.is_file():
            raise IngestError(f"output listed in manifest is missing: {item.path.name}")
        if item.expected_sha256 and file_sha256(item.path) != item.expected_sha256:
            raise IngestError(f"{item.path.name}: sha256 differs from the tool manifest")
        known = [value for value in item.input_hashes if hub.db.get(conn, value) is not None]
        unknown = sorted(set(item.input_hashes) - set(known))
        lineage = [*explicit, *known]
        attributes = {key: value for key, value in item.attributes.items() if value is not None}
        attributes["qa_manifest_id"] = manifest_record.artifact_id
        if unknown:
            attributes["unregistered_inputs"] = unknown
        registered = hub.register(conn, ArtifactInput(
            item.path, item.kind, item.producer, lineage=lineage, media_type=item.media_type,
            attributes=attributes, project_id=project_id, privacy_class=privacy_class,
        ))
        outputs.append(registered)
    return IngestResult(fmt, manifest_record, inputs, outputs, skipped)



_INPUT_KINDS = {
    ".csv": "dataset.table", ".glb": "model3d.glb", ".wav": "audio.track", ".flac": "audio.track",
    ".mp4": "video.source", ".mov": "video.source", ".webm": "video.source", ".mkv": "video.source",
    ".ifc": "bim.ifc", ".pdf": "document.scan", ".png": "document.scan", ".jpg": "document.scan",
    ".json": "qa.manifest",
}


def _input_kind(path: Path) -> str:
    kind = _INPUT_KINDS.get(path.suffix.lower())
    if kind is None:
        raise IngestError(f"cannot infer an input kind for {path.name}; register it with import-file")
    return kind
