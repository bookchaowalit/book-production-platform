from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from book_production_platform.cad_production import jobs
from book_production_platform.cad_production.errors import CadProductionError
from book_production_platform.cad_production.qa import CadQAResult
from book_production_platform.cad_production.runtime import (
    FreeCADIdentity,
    WorkerResult,
)
from tests.cad_production.conftest import BASE_PART_IR


@pytest.fixture
def valid_job(tmp_path: Path) -> dict[str, object]:
    input_path = tmp_path / "part.json"
    input_path.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    return {
        "input_path": input_path,
        "output_root": tmp_path / "output",
        "freecadcmd": tmp_path / "FreeCADCmd.exe",
        "timeout_s": 30,
    }


def fake_identity(executable_sha256: str = "a" * 64) -> FreeCADIdentity:
    return FreeCADIdentity(
        version="FreeCAD 1.1.3",
        build="Revision: 20260725 (Git shallow)",
        platform="windows-x86_64",
        executable_sha256=executable_sha256,
    )


def fake_qa() -> CadQAResult:
    return CadQAResult(
        bounds_mm=(0.0, 137.0, 0.0, 83.0, 0.0, 7.5),
        volume_mm3=83774.5355,
        solid_count=1,
        bore_count=4,
        step_relative_volume_error=0.0,
        stl_triangle_count=12,
        stl_volume_mm3=100000.0,
        tessellation_vertices=8,
        tessellation_faces=12,
        preview_profile="isometric-1024x768.v1",
    )


def install_fake_engine(monkeypatch: pytest.MonkeyPatch, *, delay_s: float = 0.0) -> list[str]:
    calls: list[str] = []
    calls_lock = threading.Lock()
    monkeypatch.setattr(jobs, "inspect_freecad_runtime", lambda _path: fake_identity())

    def fake_worker(
        _binary: Path,
        mode: str,
        _request: Path,
        root: Path,
        _timeout_s: int,
    ) -> WorkerResult:
        with calls_lock:
            calls.append(mode)
        if delay_s:
            time.sleep(delay_s)
        if mode == "build":
            for name, content in (
                ("part.FCStd", b"native document"),
                ("part.step", b"step solid"),
                ("part.stl", b"stl mesh"),
                ("tessellation.json", b"tessellation"),
            ):
                (root / name).write_bytes(content)
            return WorkerResult(0, 0.01, 0, None, {"status": "ok"})
        return WorkerResult(0, 0.01, 0, "worker_failed", None)

    def fake_qa(
        _part: object,
        root: Path,
        _binary: Path,
        _timeout_s: int,
    ) -> CadQAResult:
        (root / "preview.png").write_bytes(b"deterministic preview")
        return globals()["fake_qa"]()

    monkeypatch.setattr(jobs, "run_freecad_worker", fake_worker)
    monkeypatch.setattr(jobs, "verify_cad_outputs", fake_qa)
    return calls


def test_fresh_job_publishes_exact_artifacts_manifest_and_receipt(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_engine(monkeypatch)

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "accepted"
    assert result.replay is False
    assert result.error_code is None
    assert result.model_calls == 0
    assert result.fingerprint is not None and len(result.fingerprint) == 64
    assert result.artifact_dir == valid_job["output_root"] / result.fingerprint
    assert result.artifact_dir is not None
    expected_names = {
        "part.FCStd",
        "part.step",
        "part.stl",
        "preview.png",
        "qa-manifest.json",
    }
    assert {path.name for path in result.artifact_dir.iterdir()} == expected_names
    assert set(result.artifact_hashes) == expected_names
    assert calls == ["build"]

    manifest_path = result.artifact_dir / "qa-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt_path = valid_job["output_root"] / ".receipts" / f"{result.fingerprint}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert manifest["model_calls"] == 0
    assert manifest["monetary_cost"] == "unknown"
    assert set(manifest["artifacts"]) == expected_names - {"qa-manifest.json"}
    assert receipt["schema_version"] == "cad-job.receipt.v1"
    assert receipt["fingerprint"] == result.fingerprint
    assert set(receipt["artifacts"]) == expected_names
    for name in expected_names:
        data = (result.artifact_dir / name).read_bytes()
        assert result.artifact_hashes[name] == hashlib.sha256(data).hexdigest()
        assert receipt["artifacts"][name]["sha256"] == result.artifact_hashes[name]
        assert receipt["artifacts"][name]["bytes"] == len(data)


def test_exact_replay_verifies_hashes_without_worker_or_qa(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.artifact_dir is not None
    before = {path.name: path.read_bytes() for path in first.artifact_dir.iterdir()}

    monkeypatch.setattr(
        jobs,
        "run_freecad_worker",
        lambda *_args, **_kwargs: pytest.fail("replay must not start FreeCAD"),
    )
    monkeypatch.setattr(
        jobs,
        "verify_cad_outputs",
        lambda *_args, **_kwargs: pytest.fail("replay must not regenerate QA"),
    )
    replay = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert replay.status == "replayed"
    assert replay.replay is True
    assert replay.fingerprint == first.fingerprint
    assert replay.artifact_hashes == first.artifact_hashes
    assert replay.bytes_written == 0
    assert replay.model_calls == 0
    assert calls == ["build"]
    assert before == {path.name: path.read_bytes() for path in first.artifact_dir.iterdir()}


def test_changed_input_and_runtime_builds_have_distinct_fingerprints(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    changed_input = Path(valid_job["input_path"])
    payload = json.loads(changed_input.read_text(encoding="utf-8"))
    payload["parameters"]["thickness_mm"] = "8.000"
    changed_input.write_text(json.dumps(payload), encoding="utf-8")
    second = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert first.fingerprint != second.fingerprint
    assert first.artifact_dir != second.artifact_dir

    changed_identity = fake_identity("b" * 64)
    monkeypatch.setattr(jobs, "inspect_freecad_runtime", lambda _path: changed_identity)
    third = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert third.fingerprint != second.fingerprint


def test_invalid_input_is_rejected_before_runtime_inspection(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    Path(valid_job["input_path"]).write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(
        jobs,
        "inspect_freecad_runtime",
        lambda _path: pytest.fail("invalid input must be rejected before runtime launch"),
    )

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "invalid_part_ir"
    assert result.fingerprint is None
    assert result.artifact_dir is None
    assert result.bytes_written == 0
    assert not Path(valid_job["output_root"]).exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("template", "freeform.v1", "unsupported_template"),
        ("units", "inch", "unsupported_units"),
        ("width_mm", "501", "dimension_out_of_range"),
        ("hole_offset_x_mm", "5", "feature_constraint_violation"),
    ],
)
def test_job_preserves_specific_part_ir_error_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    expected_code: str,
) -> None:
    input_value = json.loads(json.dumps(BASE_PART_IR))
    if field in {"template", "units"}:
        input_value[field] = value
    else:
        input_value["parameters"][field] = value
    source = tmp_path / "unsupported-part.json"
    source.write_text(json.dumps(input_value), encoding="utf-8")
    monkeypatch.setattr(
        jobs,
        "inspect_freecad_runtime",
        lambda _path: pytest.fail("invalid PartIR must be rejected before runtime inspection"),
    )

    result = jobs.build_cad_job(source, tmp_path / "jobs", tmp_path / "FreeCADCmd.exe")

    assert result.status == "rejected"
    assert result.error_code == expected_code


def test_linked_output_root_is_rejected_before_writing(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)
    root = Path(valid_job["output_root"])
    root.mkdir()
    monkeypatch.setattr(jobs, "_is_link", lambda candidate: candidate == root)

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "unsafe_output_path"
    assert not (root / ".locks").exists()


def test_qa_failure_cleans_staging_without_receipt(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)

    def fail_qa(*_args: object, **_kwargs: object) -> CadQAResult:
        raise CadProductionError("native_document_invalid")

    monkeypatch.setattr(jobs, "verify_cad_outputs", fail_qa)
    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "native_document_invalid"
    assert result.artifact_dir is None
    assert result.fingerprint is not None
    root = Path(valid_job["output_root"])
    assert not (root / result.fingerprint).exists()
    assert not (root / ".receipts" / f"{result.fingerprint}.json").exists()
    assert not list((root / ".staging").glob("*"))


@pytest.mark.parametrize("tamper", ["manifest", "step", "preview", "receipt", "oversized"])
def test_tampered_artifacts_and_receipt_fail_closed_without_rewriting(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    calls = install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.artifact_dir is not None and first.fingerprint is not None
    if tamper == "receipt":
        target = Path(valid_job["output_root"]) / ".receipts" / f"{first.fingerprint}.json"
    elif tamper == "oversized":
        target = first.artifact_dir / "part.FCStd"
        monkeypatch.setattr(jobs, "MAX_ARTIFACT_BYTES", 8)
    else:
        target = first.artifact_dir / {
            "manifest": "qa-manifest.json",
            "step": "part.step",
            "preview": "preview.png",
        }[tamper]
    target.write_bytes(target.read_bytes() + b"x")
    modified = target.read_bytes()

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "replay_integrity_error"
    assert target.read_bytes() == modified
    assert calls == ["build"]


def test_linked_replay_artifact_fails_closed(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.artifact_dir is not None
    artifact = first.artifact_dir / "part.step"
    original = artifact.read_bytes()
    monkeypatch.setattr(jobs, "_is_link", lambda candidate: candidate == artifact)

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "replay_integrity_error"
    assert artifact.read_bytes() == original


@pytest.mark.parametrize("mutation", ["missing-file", "extra-file"])
def test_incomplete_or_extra_replay_state_fails_closed(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.artifact_dir is not None and first.fingerprint is not None
    if mutation == "missing-file":
        (first.artifact_dir / "part.stl").unlink()
    else:
        (first.artifact_dir / "unexpected.bin").write_bytes(b"extra")

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "replay_integrity_error"


def _crash_before_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(_path: Path, _payload: bytes) -> int:
        raise KeyboardInterrupt("simulated crash after publish")

    monkeypatch.setattr(jobs, "_write_receipt_exclusive", crash)


def test_crash_between_publish_and_receipt_is_recovered_without_rebuild(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_engine(monkeypatch)
    original_write = jobs._write_receipt_exclusive
    _crash_before_receipt(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    root = Path(valid_job["output_root"])
    [published] = [path for path in root.iterdir() if not path.name.startswith(".")]
    receipt_path = root / ".receipts" / f"{published.name}.json"
    assert not receipt_path.exists()
    before = {path.name: path.read_bytes() for path in published.iterdir()}

    monkeypatch.setattr(jobs, "_write_receipt_exclusive", original_write)
    recovered = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert recovered.status == "replayed"
    assert recovered.receipt_recovered is True
    assert recovered.to_dict()["receipt_recovered"] is True
    assert recovered.fingerprint == published.name
    assert recovered.bytes_written > 0
    assert calls == ["build"], "recovery must not start FreeCAD again"
    assert receipt_path.is_file()
    assert before == {path.name: path.read_bytes() for path in published.iterdir()}

    replay = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert replay.status == "replayed"
    assert replay.receipt_recovered is False
    assert replay.artifact_hashes == recovered.artifact_hashes


@pytest.mark.parametrize("mutation", ["step", "manifest", "missing-file", "extra-file"])
def test_missing_receipt_with_inconsistent_artifacts_fails_closed(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.artifact_dir is not None and first.fingerprint is not None
    receipt_path = Path(valid_job["output_root"]) / ".receipts" / f"{first.fingerprint}.json"
    receipt_path.unlink()
    if mutation == "step":
        target = first.artifact_dir / "part.step"
        target.write_bytes(target.read_bytes() + b"x")
    elif mutation == "manifest":
        target = first.artifact_dir / "qa-manifest.json"
        manifest = json.loads(target.read_text(encoding="utf-8"))
        manifest["fingerprint"] = "0" * 64
        target.write_bytes(jobs._json_bytes(manifest))
    elif mutation == "missing-file":
        (first.artifact_dir / "preview.png").unlink()
    else:
        (first.artifact_dir / "unexpected.bin").write_bytes(b"extra")
    before = {path.name: path.read_bytes() for path in first.artifact_dir.iterdir()}

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "replay_integrity_error"
    assert not receipt_path.exists(), "no receipt may be written for an inconsistent job"
    assert before == {path.name: path.read_bytes() for path in first.artifact_dir.iterdir()}


def test_abandoned_staging_for_the_fingerprint_is_removed(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)
    first = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]
    assert first.fingerprint is not None
    staging_root = Path(valid_job["output_root"]) / ".staging"
    stale = staging_root / f".{first.fingerprint}.staging-crashed"
    stale.mkdir()
    (stale / "part.step").write_bytes(b"half written")
    other = staging_root / f".{'f' * 64}.staging-other"
    other.mkdir()

    replay = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert replay.status == "replayed"
    assert not stale.exists()
    assert other.is_dir(), "staging for other fingerprints is not ours to remove"


def test_worker_timeout_leaves_no_published_job_or_receipt(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_engine(monkeypatch)
    monkeypatch.setattr(
        jobs,
        "run_freecad_worker",
        lambda *_args, **_kwargs: WorkerResult(0, 1.0, 0, "worker_deadline_exceeded", None),
    )

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "worker_deadline_exceeded"
    assert result.fingerprint is not None
    assert not (Path(valid_job["output_root"]) / result.fingerprint).exists()
    assert not (Path(valid_job["output_root"]) / ".receipts" / f"{result.fingerprint}.json").exists()
    assert not list((Path(valid_job["output_root"]) / ".staging").glob("*"))
    assert calls == []


def test_publication_failure_cleans_only_staging(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)
    original_rename = jobs.os.rename

    def fail_job_publish(source: str | bytes | Path, target: str | bytes | Path) -> None:
        if Path(source).parent.name == ".staging":
            raise OSError("controlled publish failure")
        original_rename(source, target)

    monkeypatch.setattr(jobs.os, "rename", fail_job_publish)

    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "publication_failed"
    assert result.fingerprint is not None
    assert not (Path(valid_job["output_root"]) / result.fingerprint).exists()
    assert not (Path(valid_job["output_root"]) / ".receipts" / f"{result.fingerprint}.json").exists()
    assert not list((Path(valid_job["output_root"]) / ".staging").glob("*"))


def test_lock_contention_returns_stable_rejection(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_engine(monkeypatch)

    @contextmanager
    def locked(_path: Path) -> Iterator[None]:
        raise CadProductionError("publication_failed")
        yield

    monkeypatch.setattr(jobs, "_job_lock", locked)
    result = jobs.build_cad_job(**valid_job)  # type: ignore[arg-type]

    assert result.status == "rejected"
    assert result.error_code == "publication_failed"
    assert result.artifact_dir is None


def test_identical_concurrent_jobs_build_once_then_replay(
    valid_job: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_fake_engine(monkeypatch, delay_s=0.15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(jobs.build_cad_job, **valid_job) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert len({result.fingerprint for result in results}) == 1
    assert sorted(result.status for result in results) == ["accepted", "replayed"]
    assert min(result.bytes_written for result in results) == 0
    assert calls == ["build"]


def test_real_freecad_job_publishes_and_replays_exact_five_outputs(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    freecad = (
        repository_root
        / ".runtime"
        / "cad"
        / "freecad-1.1.3"
        / "extracted"
        / "FreeCAD_1.1.3-Windows-x86_64-py311"
        / "FreeCADCmd.exe"
    )
    if not freecad.is_file():
        pytest.skip("the pinned local FreeCAD runtime is unavailable")
    input_path = tmp_path / "real-part.json"
    input_path.write_text(json.dumps(BASE_PART_IR), encoding="utf-8")
    output_root = tmp_path / "real-output"
    while len(str(output_root / ".staging" / ("." + "f" * 64 + ".staging-x"))) < 300:
        output_root = output_root / "deep-cad-output-component"
    arguments = {
        "input_path": input_path,
        "output_root": output_root,
        "freecadcmd": freecad,
    }

    first = jobs.build_cad_job(**arguments)
    replay = jobs.build_cad_job(**arguments)

    expected = {
        "part.FCStd",
        "part.step",
        "part.stl",
        "preview.png",
        "qa-manifest.json",
    }
    assert first.status == "accepted"
    assert replay.status == "replayed"
    assert first.fingerprint == replay.fingerprint
    assert first.artifact_dir is not None
    assert {item.name for item in first.artifact_dir.iterdir()} == expected
    assert set(first.artifact_hashes) == expected
    assert replay.artifact_hashes == first.artifact_hashes
