"""Deterministic 20-case synthetic acceptance and renderer benchmark."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import tempfile
import time
from pathlib import Path
from typing import Any

from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.qa import verify_png
from book_production_platform.scene3d.runtime import (
    MAX_TIMEOUT_SECONDS,
    _run_worker,
    compose_scene,
)
from book_production_platform.scene3d.scene_builder import RENDER_PROFILES

CASE_SCHEMA = "scene3d.acceptance-cases.v1"
BENCHMARK_SCHEMA = "scene3d.benchmark.v1"
CASE_COUNTS = {"accept": 5, "replay": 5, "reject": 8, "tamper_replay": 2}
ALLOWED_SHAPES = {"box", "canister", "bottle", "sphere", "hourglass"}
ALLOWED_REJECTIONS = {
    "extra_field",
    "schema_version",
    "path_traversal",
    "external_buffer",
    "extension",
    "animation",
    "non_triangle",
    "buffer_bounds",
}
EXPECTED_ERROR_CODES = {
    "invalid_scene_ir",
    "unsafe_glb_resource",
    "unsupported_glb_feature",
    "invalid_glb",
    "replay_integrity_error",
}
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_CYCLES_WORKER = Path(__file__).with_name("benchmark_cycles_worker.py").resolve()


def _reject_fixture(message: str) -> None:
    raise SceneInputError("invalid_benchmark_fixtures", message)


def _load_cases(fixtures_dir: Path) -> list[dict[str, Any]]:
    cases_path = fixtures_dir / "scene_cases.json"
    try:
        if cases_path.is_symlink() or cases_path.stat().st_size > 1024 * 1024:
            _reject_fixture("acceptance fixture is unsafe or too large")
        fixture = json.loads(cases_path.read_text(encoding="utf-8"))
    except SceneInputError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise SceneInputError("invalid_benchmark_fixtures", "acceptance fixture cannot be read") from error
    if not isinstance(fixture, dict) or set(fixture) != {"schema_version", "cases"}:
        _reject_fixture("acceptance fixture has an invalid shape")
    if fixture["schema_version"] != CASE_SCHEMA or not isinstance(fixture["cases"], list):
        _reject_fixture("acceptance fixture version is unsupported")
    cases = fixture["cases"]
    counts: dict[str, int] = {}
    identifiers: set[str] = set()
    accept_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            _reject_fixture("acceptance case is invalid")
        identifier = case["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier) or identifier in identifiers:
            _reject_fixture("acceptance case IDs must be unique lowercase slugs")
        identifiers.add(identifier)
        kind = case.get("kind")
        if not isinstance(kind, str):
            _reject_fixture("acceptance case kind is invalid")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "accept":
            if set(case) != {"id", "kind", "shape", "color", "render_profile"}:
                _reject_fixture("acceptance scene case has unknown fields")
            if (
                not isinstance(case["shape"], str)
                or case["shape"] not in ALLOWED_SHAPES
                or not isinstance(case["render_profile"], str)
                or case["render_profile"] not in RENDER_PROFILES
            ):
                _reject_fixture("acceptance scene case uses an unsupported preset")
            color = case["color"]
            if (
                not isinstance(color, list)
                or len(color) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in color)
            ):
                _reject_fixture("acceptance case color is invalid")
            accept_ids.add(identifier)
        elif kind == "replay":
            if set(case) != {"id", "kind", "reference"}:
                _reject_fixture("replay case has unknown fields")
        elif kind == "reject":
            if set(case) != {"id", "kind", "mutation", "expected_error"}:
                _reject_fixture("rejection case has unknown fields")
            if (
                not isinstance(case["mutation"], str)
                or case["mutation"] not in ALLOWED_REJECTIONS
                or not isinstance(case["expected_error"], str)
                or case["expected_error"] not in EXPECTED_ERROR_CODES
            ):
                _reject_fixture("rejection case uses an unsupported mutation")
        elif kind == "tamper_replay":
            if set(case) != {"id", "kind", "reference", "expected_error"}:
                _reject_fixture("tamper case has unknown fields")
            if not isinstance(case["expected_error"], str) or case["expected_error"] != "replay_integrity_error":
                _reject_fixture("tamper case must expect replay integrity rejection")
        else:
            _reject_fixture("acceptance case kind is unsupported")
    if len(cases) != 20 or counts != CASE_COUNTS:
        _reject_fixture("acceptance suite must contain five accepts, five replays, eight rejects and two tamper cases")
    for case in cases:
        if case["kind"] in {"replay", "tamper_replay"} and (
            not isinstance(case["reference"], str) or case["reference"] not in accept_ids
        ):
            _reject_fixture("replay case references a missing accepted case")
    return cases


def _box_mesh() -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[int]]:
    faces = [
        ([(-1, -0.65, 1), (1, -0.65, 1), (1, 0.65, 1), (-1, 0.65, 1)], (0, 0, 1)),
        ([(-1, 0.65, -1), (1, 0.65, -1), (1, -0.65, -1), (-1, -0.65, -1)], (0, 0, -1)),
        ([(1, -0.65, -1), (1, 0.65, -1), (1, 0.65, 1), (1, -0.65, 1)], (1, 0, 0)),
        ([(-1, 0.65, -1), (-1, -0.65, -1), (-1, -0.65, 1), (-1, 0.65, 1)], (-1, 0, 0)),
        ([(1, 0.65, -1), (-1, 0.65, -1), (-1, 0.65, 1), (1, 0.65, 1)], (0, 1, 0)),
        ([(-1, -0.65, -1), (1, -0.65, -1), (1, -0.65, 1), (-1, -0.65, 1)], (0, -1, 0)),
    ]
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for corners, normal in faces:
        start = len(positions)
        positions.extend(tuple(float(value) for value in corner) for corner in corners)
        normals.extend([tuple(float(value) for value in normal)] * 4)
        indices.extend((start, start + 1, start + 2, start, start + 2, start + 3))
    return positions, normals, indices


def _lathe_mesh(profile: list[tuple[float, float]], segments: int = 48) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[int]]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for ring_index, (radius, z_value) in enumerate(profile):
        previous = profile[max(0, ring_index - 1)]
        following = profile[min(len(profile) - 1, ring_index + 1)]
        slope = (following[0] - previous[0]) / max(1e-9, following[1] - previous[1])
        for segment in range(segments):
            angle = 2.0 * math.pi * segment / segments
            cosine, sine = math.cos(angle), math.sin(angle)
            normal_length = math.sqrt(1.0 + slope * slope)
            positions.append((radius * cosine, radius * sine, z_value))
            normals.append((cosine / normal_length, sine / normal_length, -slope / normal_length))
    for ring_index in range(len(profile) - 1):
        lower = ring_index * segments
        upper = (ring_index + 1) * segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            a, b = lower + segment, lower + next_segment
            c, d = upper + next_segment, upper + segment
            indices.extend((a, b, c, a, c, d))

    bottom_center = len(positions)
    positions.append((0.0, 0.0, profile[0][1]))
    normals.append((0.0, 0.0, -1.0))
    bottom_ring = len(positions)
    for segment in range(segments):
        angle = 2.0 * math.pi * segment / segments
        positions.append((profile[0][0] * math.cos(angle), profile[0][0] * math.sin(angle), profile[0][1]))
        normals.append((0.0, 0.0, -1.0))
    for segment in range(segments):
        indices.extend((bottom_center, bottom_ring + (segment + 1) % segments, bottom_ring + segment))

    top_center = len(positions)
    positions.append((0.0, 0.0, profile[-1][1]))
    normals.append((0.0, 0.0, 1.0))
    top_ring = len(positions)
    for segment in range(segments):
        angle = 2.0 * math.pi * segment / segments
        positions.append((profile[-1][0] * math.cos(angle), profile[-1][0] * math.sin(angle), profile[-1][1]))
        normals.append((0.0, 0.0, 1.0))
    for segment in range(segments):
        indices.extend((top_center, top_ring + segment, top_ring + (segment + 1) % segments))
    return positions, normals, indices


def _sphere_mesh(segments: int = 48, stacks: int = 24) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[int]]:
    positions: list[tuple[float, float, float]] = [(0.0, 0.0, 1.0)]
    normals: list[tuple[float, float, float]] = [(0.0, 0.0, 1.0)]
    for stack in range(1, stacks):
        phi = math.pi * stack / stacks
        for segment in range(segments):
            theta = 2.0 * math.pi * segment / segments
            position = (math.sin(phi) * math.cos(theta), math.sin(phi) * math.sin(theta), math.cos(phi))
            positions.append(position)
            normals.append(position)
    bottom = len(positions)
    positions.append((0.0, 0.0, -1.0))
    normals.append((0.0, 0.0, -1.0))
    indices: list[int] = []
    first_ring = 1
    for segment in range(segments):
        indices.extend((0, first_ring + segment, first_ring + (segment + 1) % segments))
    ring_count = stacks - 1
    for ring_index in range(ring_count - 1):
        upper = 1 + ring_index * segments
        lower = upper + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            a, b = upper + segment, upper + next_segment
            c, d = lower + next_segment, lower + segment
            indices.extend((a, d, c, a, c, b))
    last_ring = 1 + (ring_count - 1) * segments
    for segment in range(segments):
        indices.extend((bottom, last_ring + (segment + 1) % segments, last_ring + segment))
    return positions, normals, indices


def _synthetic_glb(shape: str, color: list[float], case_id: str, mutation: str | None = None) -> bytes:
    if shape == "box":
        positions, normals, indices = _box_mesh()
    elif shape == "canister":
        positions, normals, indices = _lathe_mesh([(0.62, -1.0), (0.7, -0.85), (0.7, 0.78), (0.62, 0.95), (0.62, 1.0)])
    elif shape == "bottle":
        positions, normals, indices = _lathe_mesh([(0.52, -1.0), (0.7, -0.78), (0.72, 0.18), (0.55, 0.48), (0.31, 0.68), (0.27, 0.76), (0.27, 0.96), (0.3, 1.0)])
    elif shape == "sphere":
        positions, normals, indices = _sphere_mesh()
    else:
        positions, normals, indices = _lathe_mesh([(0.68, -1.0), (0.52, -0.72), (0.3, -0.3), (0.22, 0.0), (0.3, 0.3), (0.52, 0.72), (0.68, 1.0)])

    # The fixture geometry is authored Z-up for readability, then converted to
    # glTF's Y-up basis so Blender's importer restores the intended orientation.
    positions = [(x_value, z_value, -y_value) for x_value, y_value, z_value in positions]
    normals = [(x_value, z_value, -y_value) for x_value, y_value, z_value in normals]
    flat_positions = [coordinate for vertex in positions for coordinate in vertex]
    flat_normals = [coordinate for vertex in normals for coordinate in vertex]
    position_bytes = struct.pack(f"<{len(flat_positions)}f", *flat_positions)
    normal_bytes = struct.pack(f"<{len(flat_normals)}f", *flat_normals)
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    normal_offset = len(position_bytes)
    index_offset = normal_offset + len(normal_bytes)
    binary = position_bytes + normal_bytes + index_bytes
    position_min = [min(vertex[axis] for vertex in positions) for axis in range(3)]
    position_max = [max(vertex[axis] for vertex in positions) for axis in range(3)]
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": f"book-production-synthetic:{case_id}"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0, "mode": 4}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [*color, 1.0], "metallicFactor": 0.08, "roughnessFactor": 0.4}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": normal_offset, "byteLength": len(normal_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": len(index_bytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3", "min": position_min, "max": position_max},
            {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }
    if mutation == "external_buffer":
        document["buffers"][0]["uri"] = "https://example.invalid/external.bin"
    elif mutation == "extension":
        document["extensionsUsed"] = ["EXT_unapproved"]
    elif mutation == "animation":
        document["animations"] = [{}]
    elif mutation == "non_triangle":
        document["meshes"][0]["primitives"][0]["mode"] = 1
    elif mutation == "buffer_bounds":
        document["bufferViews"][0]["byteLength"] = len(binary) + 4

    json_data = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_data += b" " * (-len(json_data) % 4)
    padded_binary = binary + b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(json_data), _JSON_CHUNK) + json_data
    chunks += struct.pack("<II", len(padded_binary), _BIN_CHUNK) + padded_binary
    total = 12 + len(chunks)
    return struct.pack("<4sII", b"glTF", 2, total) + chunks


def _scene_ir(render_profile: str) -> dict[str, Any]:
    return {
        "schema_version": "scene-ir.v1",
        "asset": {"path": "product.glb"},
        "recipe": "product-hero.studio.v1",
        "composition": "three-quarter.v1",
        "lighting": "softbox-three-point.v1",
        "background": "studio-neutral.v1",
        "material_preset": "preserve-source.v1",
        "render_profile": render_profile,
    }


def _create_case_inputs(work_root: Path, case: dict[str, Any]) -> tuple[Path, Path, Path]:
    case_root = work_root / "cases" / case["id"]
    input_root = case_root / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    scene_value = _scene_ir(case.get("render_profile", "square-1024.v1"))
    mutation = None
    if case["kind"] == "reject":
        mutation = case["mutation"]
        if mutation == "extra_field":
            scene_value["python"] = "print(1)"
        elif mutation == "schema_version":
            scene_value["schema_version"] = "scene-ir.v99"
        elif mutation == "path_traversal":
            scene_value["asset"]["path"] = "../product.glb"
    shape = case.get("shape", "box")
    color = case.get("color", [0.3, 0.5, 0.75])
    asset = input_root / "product.glb"
    asset.write_bytes(_synthetic_glb(shape, color, case["id"], mutation))
    scene_path = input_root / "scene.json"
    scene_path.write_text(json.dumps(scene_value, ensure_ascii=False), encoding="utf-8")
    return scene_path, input_root, work_root / "jobs"


def _case_bytes(result: Any) -> int:
    return sum((result.artifact_dir / name).stat().st_size for name in ("scene.blend", "render.png", "qa-manifest.json"))


def _atomic_report(output_path: Path, report: dict[str, Any]) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise SceneInputError("benchmark_output_exists", "benchmark output already exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
        os.rename(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _cycles_candidate(
    blender_bin: Path,
    blend_path: Path,
    png_path: Path,
    worker_dir: Path,
    timeout_s: int,
) -> float:
    worker_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(blender_bin),
        "--background",
        "--factory-startup",
        "--python",
        str(_CYCLES_WORKER),
        "--",
        "--blend",
        str(blend_path),
        "--png",
        str(png_path),
    ]
    return _run_worker(command, cwd=worker_dir, timeout_s=timeout_s)


def run_benchmark(
    fixtures_dir: Path,
    output_path: Path,
    blender_bin: Path,
    timeout_s: int = MAX_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the pinned 20-case synthetic suite and save reviewable golden renders."""
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 0 < timeout_s <= MAX_TIMEOUT_SECONDS:
        raise SceneInputError("invalid_timeout", "timeout must be between 1 and 300 seconds")
    cases = _load_cases(fixtures_dir)
    output_path = Path(output_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SceneInputError("unsafe_output_path", "benchmark output directory is unavailable") from error
    if output_path.exists() or output_path.is_symlink():
        raise SceneInputError("benchmark_output_exists", "benchmark output already exists")
    golden_dir = output_path.with_name(f"{output_path.stem}.golden-renders")
    if golden_dir.exists() or golden_dir.is_symlink():
        raise SceneInputError("benchmark_output_exists", "golden render directory already exists")
    blender_bin = Path(blender_bin).expanduser().resolve()
    records: list[dict[str, Any]] = []
    accepted: dict[str, Any] = {}
    contexts: dict[str, tuple[Path, Path, Path]] = {}
    fresh_latencies: list[float] = []
    replay_latencies: list[float] = []
    eevee_render_latencies: list[float] = []
    rejected_by_code: dict[str, int] = {}
    observed = {"accepted": 0, "replayed": 0, "rejected": 0}
    bytes_written = 0
    bytes_reused = 0
    qa_failures = 0
    unexpected: list[str] = []
    golden_names: list[str] = []
    cycle_names: list[str] = []
    cycle_latencies: list[float] = []
    cycle_errors: list[str] = []
    golden_stage: Path | None = None
    started = time.perf_counter()

    try:
        with tempfile.TemporaryDirectory(prefix="scene3d-benchmark-", dir=output_path.parent) as work_name:
            work_root = Path(work_name)
            golden_stage = work_root / "goldens"
            golden_stage.mkdir()
            (golden_stage / "cycles").mkdir()

            for case in cases:
                identifier = case["id"]
                kind = case["kind"]
                case_started = time.perf_counter()
                error_code: str | None = None
                fingerprint: str | None = None
                actual = "rejected"
                result_label = "pass"
                case_bytes = 0
                model_calls = 0

                if kind == "accept":
                    context = _create_case_inputs(work_root, case)
                    contexts[identifier] = context
                    try:
                        result = compose_scene(*context, blender_bin, timeout_s=timeout_s)
                        actual = result.status
                        fingerprint = result.fingerprint[:12]
                        model_calls = result.model_calls
                        if result.status != "accepted":
                            result_label = "unexpected_status"
                            unexpected.append(identifier)
                        else:
                            accepted[identifier] = result
                            case_bytes = _case_bytes(result)
                            bytes_written += case_bytes
                            fresh_latencies.append(time.perf_counter() - case_started)
                            manifest = json.loads((result.artifact_dir / "qa-manifest.json").read_text(encoding="utf-8"))
                            eevee_render_latencies.append(float(manifest["timing_seconds"]["fresh_reopen_render"]))
                            golden_name = f"{identifier}.png"
                            shutil.copyfile(result.artifact_dir / "render.png", golden_stage / golden_name)
                            golden_names.append(golden_name)
                    except SceneInputError as error:
                        error_code = error.code
                        result_label = "unexpected_error"
                        unexpected.append(identifier)

                elif kind == "replay":
                    reference = case["reference"]
                    context = contexts.get(reference)
                    if context is None or reference not in accepted:
                        result_label = "missing_reference"
                        unexpected.append(identifier)
                    else:
                        try:
                            result = compose_scene(*context, blender_bin, timeout_s=timeout_s)
                            actual = result.status
                            fingerprint = result.fingerprint[:12]
                            model_calls = result.model_calls
                            case_bytes = _case_bytes(result)
                            bytes_reused += case_bytes
                            replay_latencies.append(time.perf_counter() - case_started)
                            if result.status != "replayed" or not result.replay:
                                result_label = "unexpected_status"
                                unexpected.append(identifier)
                        except SceneInputError as error:
                            error_code = error.code
                            result_label = "unexpected_error"
                            unexpected.append(identifier)

                elif kind == "reject":
                    context = _create_case_inputs(work_root, case)
                    try:
                        result = compose_scene(*context, blender_bin, timeout_s=timeout_s)
                        actual = result.status
                        fingerprint = result.fingerprint[:12]
                        model_calls = result.model_calls
                        result_label = "unexpected_accept"
                        unexpected.append(identifier)
                    except SceneInputError as error:
                        error_code = error.code
                        rejected_by_code[error.code] = rejected_by_code.get(error.code, 0) + 1
                        if error.code != case["expected_error"]:
                            result_label = "wrong_error"
                            unexpected.append(identifier)

                else:
                    reference = case["reference"]
                    result = accepted.get(reference)
                    context = contexts.get(reference)
                    if result is None or context is None:
                        result_label = "missing_reference"
                        unexpected.append(identifier)
                    else:
                        (result.artifact_dir / "render.png").write_bytes(b"benchmark tamper fixture")
                        try:
                            replay_result = compose_scene(*context, blender_bin, timeout_s=timeout_s)
                            actual = replay_result.status
                            fingerprint = replay_result.fingerprint[:12]
                            result_label = "unexpected_accept"
                            unexpected.append(identifier)
                        except SceneInputError as error:
                            error_code = error.code
                            rejected_by_code[error.code] = rejected_by_code.get(error.code, 0) + 1
                            if error.code != case["expected_error"]:
                                result_label = "wrong_error"
                                unexpected.append(identifier)
                            else:
                                qa_failures += 1

                observed[actual] = observed.get(actual, 0) + 1
                records.append(
                    {
                        "id": identifier,
                        "kind": kind,
                        "expected": case.get("expected_error", "accepted" if kind == "accept" else "replayed" if kind == "replay" else "rejected"),
                        "actual": actual,
                        "result": result_label,
                        "error_code": error_code,
                        "fingerprint_prefix": fingerprint,
                        "latency_seconds": round(time.perf_counter() - case_started, 3),
                        "artifact_bytes": case_bytes,
                        "model_calls": model_calls,
                    }
                )

            for case in cases:
                if case["kind"] != "accept" or case["id"] not in accepted:
                    continue
                result = accepted[case["id"]]
                candidate_path = work_root / "cycles" / f"{case['id']}.png"
                expected_size = RENDER_PROFILES[case["render_profile"]]
                try:
                    elapsed = _cycles_candidate(
                        blender_bin,
                        result.artifact_dir / "scene.blend",
                        candidate_path,
                        work_root / "cycles" / f"{case['id']}-worker",
                        timeout_s,
                    )
                    verify_png(candidate_path, expected_size)
                    cycle_latencies.append(elapsed)
                    cycle_name = f"cycles/{case['id']}.png"
                    shutil.copyfile(candidate_path, golden_stage / cycle_name)
                    cycle_names.append(cycle_name)
                except SceneInputError as error:
                    cycle_errors.append(error.code)

            expected_observed = {"accepted": 5, "replayed": 5, "rejected": 10}
            counts_match = observed == expected_observed
            engine_candidate_passed = len(cycle_latencies) == 5 and len(cycle_errors) == 0
            model_calls = sum(record["model_calls"] for record in records)
            status = (
                "passed"
                if counts_match and not unexpected and engine_candidate_passed and qa_failures == 2 and model_calls == 0
                else "failed"
            )
            report = {
                "schema_version": BENCHMARK_SCHEMA,
                "status": status,
                "cases_total": len(records),
                "expected_counts": expected_observed,
                "observed_counts": observed,
                "unexpected_cases": unexpected,
                "qa_failures": {"expected": 2, "observed": qa_failures},
                "rejected_by_code": dict(sorted(rejected_by_code.items())),
                "fresh_latency_seconds": {
                    "samples": [round(value, 3) for value in fresh_latencies],
                    "mean": round(sum(fresh_latencies) / len(fresh_latencies), 3) if fresh_latencies else None,
                },
                "replay_latency_seconds": {
                    "samples": [round(value, 3) for value in replay_latencies],
                    "mean": round(sum(replay_latencies) / len(replay_latencies), 3) if replay_latencies else None,
                },
                "bytes_written": bytes_written,
                "bytes_reused": bytes_reused,
                "model_calls": model_calls,
                "monetary_cost": "unknown",
                "engine_comparison": {
                    "eevee": {
                        "engine": "BLENDER_EEVEE",
                        "samples": 64,
                        "fresh_reopen_render_seconds": [round(value, 3) for value in eevee_render_latencies],
                        "mean_seconds": round(sum(eevee_render_latencies) / len(eevee_render_latencies), 3) if eevee_render_latencies else None,
                    },
                    "cycles": {
                        "engine": "CYCLES",
                        "device": "CPU",
                        "samples": 32,
                        "fresh_reopen_render_seconds": [round(value, 3) for value in cycle_latencies],
                        "mean_seconds": round(sum(cycle_latencies) / len(cycle_latencies), 3) if cycle_latencies else None,
                        "errors": cycle_errors,
                    },
                    "visual_review": "required; renderer timing is not an aesthetic score",
                },
                "golden_render_directory": golden_dir.name,
                "golden_renders": golden_names,
                "cycles_candidate_renders": cycle_names,
                "wall_seconds": round(time.perf_counter() - started, 3),
                "cases": records,
            }
            os.rename(golden_stage, golden_dir)
            _atomic_report(output_path, report)
            return report
    except SceneInputError:
        raise
    except OSError as error:
        raise SceneInputError("benchmark_io_error", "benchmark artifacts could not be written") from error
    finally:
        if golden_stage is not None and golden_stage.exists():
            shutil.rmtree(golden_stage, ignore_errors=True)
