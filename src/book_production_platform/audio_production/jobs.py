"""Replay-safe local WAV mastering jobs powered by FFmpeg."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

from book_production_platform.audio_ir import AudioIR
from book_production_platform.production_trace import TraceRecorder

from .errors import AudioProductionError
from .manual import build_audacity_guide
from .presets import (
    MASTERING_RECIPE_VERSION,
    MAX_LOCK_WAIT_SECONDS,
    MAX_PROCESS_TIMEOUT_SECONDS,
    MAX_SOURCE_BYTES,
    OUTPUT_CODEC,
    OUTPUT_SAMPLE_RATE,
    PRESETS,
    MasteringPreset,
)
from .qa import (
    LoudnessStats,
    inspect_pcm_wave,
    master_qa,
    parse_loudness_stats,
)
from .runner import ProcessOutput, run_process

_MANIFEST_SCHEMA = "audio-master.qa.v1"
_RECEIPT_SCHEMA = "audio-master.receipt.v1"
_MAX_IR_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_RECEIPT_BYTES = 4096
TOOL_ID = "audio-master-v1a"
ENGINE_VERSION = "audio-master.v1a/ffmpeg-loudnorm-2pass"
FAILED_TRACE_DIR = "failed"


@dataclass(frozen=True)
class AudioJobResult:
    status: str
    fingerprint: str
    artifact_dir: Path | None
    master_path: Path | None
    manifest_path: Path | None
    artifact_hashes: dict[str, str]
    replayed: bool = False
    model_calls: int = 0
    bytes_written: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fingerprint": self.fingerprint,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir else None,
            "master_path": str(self.master_path) if self.master_path else None,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "artifact_hashes": self.artifact_hashes,
            "replayed": self.replayed,
            "model_calls": self.model_calls,
            "bytes_written": self.bytes_written,
            "error_code": self.error_code,
        }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path, deadline: float | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if deadline is not None and time.monotonic() >= deadline:
                raise AudioProductionError("deadline_exceeded", "Audio processing deadline was exceeded")
            digest.update(block)
    return digest.hexdigest()


def _safe_path_components(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            return False
    return True


def _resolve_source(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    reserved_windows_names = {"CON", "PRN", "AUX", "NUL"}
    reserved_windows_names.update(f"COM{number}" for number in range(1, 10))
    reserved_windows_names.update(f"LPT{number}" for number in range(1, 10))
    if (
        not relative
        or "\\" in relative
        or PurePosixPath(relative).is_absolute()
        or PureWindowsPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or any(
            part in {"", ".", ".."}
            or part != part.rstrip(" .")
            or ":" in part
            or any(ord(character) < 32 for character in part)
            or part.rstrip(" .").split(".", 1)[0].upper() in reserved_windows_names
            for part in parts
        )
    ):
        raise AudioProductionError("unsafe_audio_path", "AudioIR source path must be a safe relative WAV path")
    root = root.absolute()
    if not _safe_path_components(root):
        raise AudioProductionError("unsafe_audio_path", "Input root contains a symlink")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise AudioProductionError("unsafe_audio_path", "Input root is unavailable") from error
    if not resolved_root.is_dir():
        raise AudioProductionError("unsafe_audio_path", "Input root must be a directory")
    current = resolved_root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise AudioProductionError("unsafe_audio_path", "Symlinked source paths are not supported")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise AudioProductionError("unsafe_audio_path", "Audio source is unavailable") from error
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise AudioProductionError("unsafe_audio_path", "Audio source is outside the input root")
    return resolved


def _find_ffmpeg(configured: str | Path | None) -> Path:
    candidate = str(configured) if configured is not None else os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not candidate:
        raise AudioProductionError("ffmpeg_unavailable", "Set FFMPEG_BIN or install FFmpeg")
    found = shutil.which(candidate) if not Path(candidate).exists() else candidate
    if not found:
        raise AudioProductionError("ffmpeg_unavailable", "FFmpeg executable is unavailable")
    path = Path(found)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AudioProductionError("ffmpeg_unavailable", "FFmpeg executable is unavailable") from error
    if not resolved.is_file():
        raise AudioProductionError("ffmpeg_unavailable", "FFmpeg executable is not a file")
    return resolved


def _tool_version(executable: Path, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AudioProductionError("deadline_exceeded", "Audio processing deadline was exceeded")
    result = run_process([str(executable), "-version"], min(10.0, remaining))
    if result.return_code != 0 or result.truncated:
        raise AudioProductionError("ffmpeg_version_failed", "FFmpeg version could not be verified")
    version = (result.stdout or result.stderr).decode("utf-8", errors="replace").splitlines()
    if not version or not version[0].startswith("ffmpeg version "):
        raise AudioProductionError("ffmpeg_version_failed", "FFmpeg version could not be verified")
    return version[0][:300]


def _loudnorm_graph(preset: MasteringPreset, *, mono_measurement: LoudnessStats | None = None) -> str:
    values = [
        f"I={preset.integrated_lufs:.1f}",
        f"TP={preset.true_peak_dbtp:.1f}",
        f"LRA={preset.loudness_range_lu:.1f}",
        f"dual_mono={'true' if preset.dual_mono else 'false'}",
    ]
    if mono_measurement is not None:
        values.extend(
            [
                f"measured_I={mono_measurement.integrated_lufs:.6f}",
                f"measured_TP={mono_measurement.true_peak_dbtp:.6f}",
                f"measured_LRA={mono_measurement.loudness_range_lu:.6f}",
                f"measured_thresh={mono_measurement.threshold_lufs:.6f}",
                f"offset={mono_measurement.target_offset_db:.6f}",
                "linear=true",
            ]
        )
    values.append("print_format=json")
    return "loudnorm=" + ":".join(values)


def _filter_graph(preset: MasteringPreset, *, mono_measurement: LoudnessStats | None = None, chain: bool = True) -> str:
    """The preset's fixed clean-up chain (in order) followed by ``loudnorm``."""

    stages = [stage.graph() for stage in preset.chain] if chain else []
    return ",".join([*stages, _loudnorm_graph(preset, mono_measurement=mono_measurement)])


def _input_args(executable: Path, source: Path) -> list[str]:
    return [
        str(executable),
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        "wav",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
    ]


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AudioProductionError("deadline_exceeded", "Audio processing deadline was exceeded")
    return remaining


def _run_checked(argv: list[str], deadline: float) -> ProcessOutput:
    result = run_process(argv, min(MAX_PROCESS_TIMEOUT_SECONDS, _remaining(deadline)))
    if result.truncated:
        raise AudioProductionError("ffmpeg_output_limit", "FFmpeg diagnostic output exceeded its limit")
    if result.return_code != 0:
        raise AudioProductionError("ffmpeg_failed", "FFmpeg could not process the WAV file")
    return result


def _measure(
    executable: Path, source: Path, preset: MasteringPreset, deadline: float, chain: bool = False
) -> tuple[LoudnessStats, float]:
    """Run a measurement pass; ``chain`` puts the preset's clean-up filters before ``loudnorm``."""

    result = _run_checked(
        _input_args(executable, source) + ["-af", _filter_graph(preset, chain=chain), "-f", "null", "-"],
        deadline,
    )
    return parse_loudness_stats(result.stderr), result.elapsed_seconds


@contextmanager
def _exclusive_lock(lock_path: Path, deadline: float) -> Iterator[None]:
    if lock_path.is_symlink() or not _safe_path_components(lock_path.parent.absolute()):
        raise AudioProductionError("unsafe_output_path", "Job lock path is unsafe")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(f"{os.getpid()}:{time.time_ns()}".encode()).hexdigest()
    lock_deadline = min(deadline, time.monotonic() + MAX_LOCK_WAIT_SECONDS)
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            if time.monotonic() >= lock_deadline:
                raise AudioProductionError("job_lock_timeout", "Another audio job owns this fingerprint") from error
            time.sleep(min(0.05, max(0.0, lock_deadline - time.monotonic())))
        except OSError as error:
            raise AudioProductionError("unsafe_output_path", "Audio job lock cannot be created") from error
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
            owned = os.fstat(handle.fileno())
        yield
    finally:
        try:
            stat = lock_path.stat()
            if lock_path.read_text(encoding="ascii") == token and (stat.st_dev, stat.st_ino) == (owned.st_dev, owned.st_ino):
                lock_path.unlink()
        except (OSError, UnboundLocalError):
            pass


def _read_replay(artifact_dir: Path, fingerprint: str, deadline: float) -> AudioJobResult | None:
    if not artifact_dir.exists():
        return None
    receipt = artifact_dir / ".receipt.json"
    manifest = artifact_dir / "qa-manifest.json"
    master = artifact_dir / "master.wav"
    if artifact_dir.is_symlink() or any(path.is_symlink() for path in (receipt, manifest, master)):
        raise AudioProductionError("replay_integrity_error", "Stored audio output failed integrity verification")
    try:
        if receipt.stat().st_size > _MAX_RECEIPT_BYTES or manifest.stat().st_size > _MAX_MANIFEST_BYTES:
            raise OSError("oversized replay receipt")
        if master.stat().st_size > MAX_SOURCE_BYTES:
            raise OSError("oversized master artifact")
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        expected = receipt_payload["hashes"]
        actual = {
            "master.wav": _sha256_file(master, deadline),
            "qa-manifest.json": _sha256_file(manifest, deadline),
        }
        if (
            receipt_payload.get("schema_version") != _RECEIPT_SCHEMA
            or receipt_payload.get("fingerprint") != fingerprint
            or expected != actual
            or manifest_payload.get("fingerprint") != fingerprint
            or manifest_payload.get("status") != "accepted"
        ):
            raise ValueError("receipt mismatch")
    except AudioProductionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise AudioProductionError("replay_integrity_error", "Stored audio output failed integrity verification") from error
    return AudioJobResult(
        status="accepted",
        fingerprint=fingerprint,
        artifact_dir=artifact_dir,
        master_path=master,
        manifest_path=manifest,
        artifact_hashes=actual,
        replayed=True,
        model_calls=0,
        bytes_written=0,
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _reject(code: str) -> AudioJobResult:
    return AudioJobResult("rejected", "", None, None, None, {}, error_code=code)


def _trace_failure(trace: TraceRecorder, root: Path, code: str) -> None:
    """Record rejected jobs in the history too; tracing must never mask the real failure.

    Failed jobs are traced under ``<output_root>/failed/<job_id>/``. Nothing is
    written when the output root itself is unsafe (a symlink or not a directory).
    """

    try:
        if code == "unsafe_output_path" or not _safe_path_components(root) or (root.exists() and (root.is_symlink() or not root.is_dir())):
            return
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=root / FAILED_TRACE_DIR / trace.job_id, output_root=root)
    except OSError:
        pass


def master_audio(
    audio_ir: AudioIR | dict[str, Any],
    input_root: str | Path,
    output_root: str | Path,
    ffmpeg: str | Path | None = None,
    *,
    timeout_s: int = MAX_PROCESS_TIMEOUT_SECONDS,
) -> AudioJobResult:
    """Master one local PCM WAV, publish an integrity-checked WAV + QA report, and trace the job.

    Fresh accepted jobs write ``trace.json``, ``ir.json``, and
    ``MANUAL-STEPS.md`` (Audacity) next to ``master.wav``; rejected jobs write
    a trace under ``failed/<job_id>/``; both append ``<output_root>/history.jsonl``.
    Exact replays return the stored artifacts and write nothing.
    """
    root = Path(output_root).absolute()
    trace = TraceRecorder(
        tool_id=TOOL_ID,
        job_id=str(uuid.uuid4()),
        engine_version=ENGINE_VERSION,
        recipe_version=MASTERING_RECIPE_VERSION,
        schema_version="audio-ir.v1",
    )
    try:
        trace.input_json("audio_ir", audio_ir.model_dump(mode="json") if isinstance(audio_ir, AudioIR) else audio_ir)
    except (TypeError, ValueError):
        trace.inputs.append({"name": "audio_ir", "role": "request", "sha256": None})
    result = _master(audio_ir, input_root, root, ffmpeg, timeout_s=timeout_s, trace=trace)
    if result.status != "accepted":
        _trace_failure(trace, root, result.error_code or "rejected")
    return result


def _master(
    audio_ir: AudioIR | dict[str, Any],
    input_root: str | Path,
    root: Path,
    ffmpeg: str | Path | None,
    *,
    timeout_s: int,
    trace: TraceRecorder,
) -> AudioJobResult:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 1 <= timeout_s <= MAX_PROCESS_TIMEOUT_SECONDS:
        return _reject("invalid_timeout")
    try:
        with trace.step("validate", "parse_audio_ir"):
            parsed = audio_ir if isinstance(audio_ir, AudioIR) else AudioIR.model_validate(audio_ir)
    except ValidationError:
        return _reject("invalid_audio_ir")
    started = time.monotonic()
    deadline = started + timeout_s
    try:
        preset = PRESETS[parsed.preset]
        with trace.step("validate", "inspect_source", preset=preset.name) as inspect_step:
            source = _resolve_source(Path(input_root), parsed.source_path)
            source_hash = _sha256_file(source, deadline)
            source_info = inspect_pcm_wave(source)
            trace.inputs.append({"name": "source", "role": "source", "file": source.name, "bytes": source_info.byte_size, "sha256": source_hash})
            inspect_step.params.update({
                "channels": source_info.channels,
                "sample_rate": source_info.sample_rate,
                "sample_width_bytes": source_info.sample_width_bytes,
                "duration_seconds": round(source_info.duration_seconds, 6),
            })
            if source_info.channels != preset.channels:
                raise AudioProductionError("preset_channel_mismatch", "Selected preset requires a different channel count")
        with trace.step("toolchain", "ffmpeg_version") as version_step:
            executable = _find_ffmpeg(ffmpeg)
            version = _tool_version(executable, deadline)
            version_step.params["ffmpeg_version"] = version
        ir_payload = parsed.model_dump(mode="json")
        ir_hash = hashlib.sha256(_canonical_json(ir_payload)).hexdigest()
        fingerprint_payload: dict[str, Any] = {
            "recipe": MASTERING_RECIPE_VERSION,
            "audio_ir_sha256": ir_hash,
            "source_sha256": source_hash,
            "ffmpeg_version": version,
            "preset": preset.name,
        }
        if preset.chain:
            # Presets without a clean-up chain keep their original fingerprints.
            fingerprint_payload["filter_chain"] = [stage.graph() for stage in preset.chain]
        fingerprint = hashlib.sha256(_canonical_json(fingerprint_payload)).hexdigest()
        if not _safe_path_components(root):
            raise AudioProductionError("unsafe_output_path", "Output root contains a symlink")
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise AudioProductionError("unsafe_output_path", "Output root is not a safe directory")
        root.mkdir(parents=True, exist_ok=True)
        artifact_dir = root / fingerprint
        lock = root / ".locks" / f"{fingerprint}.lock"
        with _exclusive_lock(lock, deadline):
            replay = _read_replay(artifact_dir, fingerprint, deadline)
            if replay is not None:
                return replay
            if artifact_dir.exists() or artifact_dir.is_symlink():
                raise AudioProductionError("output_collision", "Fingerprint output path already exists")
            staging_root = root / ".staging"
            if staging_root.is_symlink():
                raise AudioProductionError("unsafe_output_path", "Staging path is unsafe")
            staging_root.mkdir(exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=f"{fingerprint}.", dir=staging_root))
            master_path = staging_dir / "master.wav"
            try:
                with trace.step("analyze", "measure_loudness", graph=_filter_graph(preset)) as measure_step:
                    input_stats, analysis_seconds = _measure(executable, source, preset, deadline, bool(preset.chain))
                    measure_step.params["measured"] = input_stats.to_dict()
                if input_stats.integrated_lufs <= -70:
                    raise AudioProductionError("silent_audio", "Silent audio cannot be loudness mastered")
                render_graph = _filter_graph(preset, mono_measurement=input_stats)
                render_started = time.monotonic()
                with trace.step(
                    "render", "ffmpeg_master", graph=render_graph, chain=[stage.to_dict() for stage in preset.chain],
                    sample_rate=OUTPUT_SAMPLE_RATE, codec=OUTPUT_CODEC, strip_metadata=True,
                ) as render_step:
                    render_result = _run_checked(
                        _input_args(executable, source)
                        + [
                            "-af",
                            render_graph,
                            "-ar",
                            str(OUTPUT_SAMPLE_RATE),
                            "-c:a",
                            OUTPUT_CODEC,
                            "-map_metadata",
                            "-1",
                            "-map_metadata:s:a",
                            "-1",
                            "-map_chapters",
                            "-1",
                            "-f",
                            "wav",
                            "-y",
                            str(master_path),
                        ],
                        deadline,
                    )
                    render_stats = parse_loudness_stats(render_result.stderr)
                    render_step.params["normalization_type"] = render_stats.normalization_type
                    render_step.outputs = ["master.wav"]
                render_seconds = max(render_result.elapsed_seconds, time.monotonic() - render_started)
                with trace.step("qa", "master_qa") as qa_step:
                    output_info = inspect_pcm_wave(master_path, output=True)
                    output_stats, qa_measure_seconds = _measure(executable, master_path, preset, deadline)
                    checks = master_qa(source_info, output_info, output_stats, preset)
                    qa_step.params.update({"measured": output_stats.to_dict(), "checks": checks})
                    if not all(checks.values()):
                        qa_step.status = "failed"
                        raise AudioProductionError("audio_qa_failed", "Master WAV did not meet preset QA targets")
                if _sha256_file(source, deadline) != source_hash:
                    raise AudioProductionError("source_changed_during_processing", "Source WAV changed while it was being mastered")
                master_hash = _sha256_file(master_path, deadline)
                preset_record: dict[str, Any] = {
                    "name": preset.name,
                    "integrated_lufs": preset.integrated_lufs,
                    "true_peak_dbtp": preset.true_peak_dbtp,
                    "loudness_range_lu": preset.loudness_range_lu,
                    "dual_mono": preset.dual_mono,
                }
                if preset.chain:
                    preset_record["filter_chain"] = [stage.to_dict() for stage in preset.chain]
                manifest: dict[str, Any] = {
                    "schema_version": _MANIFEST_SCHEMA,
                    "status": "accepted",
                    "fingerprint": fingerprint,
                    "audio_id": parsed.audio_id,
                    "audio_ir_version": parsed.schema_version,
                    "audio_ir_sha256": ir_hash,
                    "source_sha256": source_hash,
                    "source_relative_path": parsed.source_path,
                    "recipe_version": MASTERING_RECIPE_VERSION,
                    "ffmpeg_version": version,
                    "preset": preset_record,
                    "measurements": {
                        "before": input_stats.to_dict(),
                        "after": output_stats.to_dict(),
                    },
                    "render_normalization_type": render_stats.normalization_type,
                    "source": {
                        "channels": source_info.channels,
                        "sample_rate": source_info.sample_rate,
                        "sample_width_bytes": source_info.sample_width_bytes,
                        "duration_seconds": round(source_info.duration_seconds, 6),
                        "bytes": source_info.byte_size,
                    },
                    "output": {
                        "filename": "master.wav",
                        "media_type": "audio/wav; codec=pcm_s24le; sample_rate=48000",
                        "channels": output_info.channels,
                        "sample_rate": output_info.sample_rate,
                        "sample_width_bytes": output_info.sample_width_bytes,
                        "duration_seconds": round(output_info.duration_seconds, 6),
                        "bytes": output_info.byte_size,
                        "sha256": master_hash,
                    },
                    "qa": {
                        "passed": True,
                        "checks": checks,
                        "integrated_loudness_tolerance_lu": 0.5,
                        "true_peak_tolerance_db": 0.1,
                    },
                    "timing_seconds": {
                        "analysis": round(analysis_seconds, 3),
                        "render": round(render_seconds, 3),
                        "qa_measurement": round(qa_measure_seconds, 3),
                        "total": round(time.monotonic() - started, 3),
                    },
                    "bytes_written": output_info.byte_size,
                    "model_calls": 0,
                    "monetary_cost": "unknown",
                }
                manifest_path = staging_dir / "qa-manifest.json"
                manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
                _atomic_write(manifest_path, manifest_bytes)
                hashes = {
                    "master.wav": master_hash,
                    "qa-manifest.json": _sha256_file(manifest_path, deadline),
                }
                receipt = {"schema_version": _RECEIPT_SCHEMA, "fingerprint": fingerprint, "hashes": hashes}
                _atomic_write(staging_dir / ".receipt.json", _canonical_json(receipt) + b"\n")
                if artifact_dir.exists() or artifact_dir.is_symlink():
                    raise AudioProductionError("output_collision", "Fingerprint output path already exists")
                staging_dir.replace(artifact_dir)
                trace.record_step("publish", "publish_master", outputs=["master.wav", "qa-manifest.json"], fingerprint=fingerprint)
                _finish_trace(trace, parsed, preset, manifest, artifact_dir, root)
                return AudioJobResult(
                    status="accepted",
                    fingerprint=fingerprint,
                    artifact_dir=artifact_dir,
                    master_path=artifact_dir / "master.wav",
                    manifest_path=artifact_dir / "qa-manifest.json",
                    artifact_hashes=hashes,
                    bytes_written=output_info.byte_size + len(manifest_bytes),
                )
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
    except AudioProductionError as error:
        return _reject(error.code)
    except OSError:
        return _reject("audio_io_error")


def _finish_trace(
    trace: TraceRecorder,
    parsed: AudioIR,
    preset: MasteringPreset,
    manifest: dict[str, Any],
    artifact_dir: Path,
    root: Path,
) -> None:
    """Write ``ir.json``, ``trace.json``, ``MANUAL-STEPS.md`` next to the published master (best effort)."""

    try:
        ir_payload = parsed.model_dump(mode="json")
        _atomic_write(artifact_dir / "ir.json", json.dumps(ir_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        trace.ir_document(ir_payload, schema=parsed.schema_version, path=artifact_dir / "ir.json")
        trace.artifact(artifact_dir / "master.wav", media_type="audio/wav")
        trace.artifact(artifact_dir / "qa-manifest.json", media_type="application/json")
        checks = manifest["qa"]["checks"]
        trace.finish(
            status="accepted",
            job_dir=artifact_dir,
            output_root=root,
            guide=build_audacity_guide(parsed, preset, manifest),
            qa={"status": "passed", "checks": [name for name, passed in checks.items() if passed]},
        )
    except OSError:
        # The master is already published and receipt-verified; a trace write
        # failure must not turn it into a rejected job.
        pass


__all__ = ["FAILED_TRACE_DIR", "TOOL_ID", "AudioJobResult", "master_audio"]
