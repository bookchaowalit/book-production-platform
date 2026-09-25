from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.cad_production import runtime
from book_production_platform.cad_production.errors import CadProductionError

VERSION_OUTPUT = "FreeCAD 1.1.3 Revision: 20260725 (Git shallow)\n"


def setup_fake_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version_output: str = VERSION_OUTPUT,
    expected_hash: str | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    binary = tmp_path / "FreeCADCmd.exe"
    binary.write_bytes(b"pinned test executable")
    executable_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    lock = {
        "schema_version": "freecad-runtime.v1",
        "source_url": "https://example.invalid/freecad.7z",
        "archive_sha256": "a" * 64,
        "version": "FreeCAD 1.1.3",
        "build": "Revision: 20260725 (Git shallow)",
        "platform": "windows-x86_64",
        "executable_sha256": expected_hash or executable_hash,
        "worker_protocol": "freecad-worker.v1",
        "preview_profile": "isometric-1024x768.v1",
    }
    monkeypatch.setattr(runtime, "_load_runtime_lock", lambda: lock)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "AMD64")
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=version_output, stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return binary, calls


def test_inspect_accepts_exact_pinned_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary, calls = setup_fake_runtime(tmp_path, monkeypatch)

    identity = runtime.inspect_freecad_runtime(binary)

    assert identity.version == "FreeCAD 1.1.3"
    assert identity.build == "Revision: 20260725 (Git shallow)"
    assert identity.platform == "windows-x86_64"
    assert identity.executable_sha256 == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] <= 15


@pytest.mark.parametrize(
    "version_output",
    [
        "FreeCAD 1.1.2 Revision: 20260725 (Git shallow)",
        "FreeCAD 1.1.3 Revision: wrong-build",
    ],
)
def test_inspect_rejects_version_or_build_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version_output: str
) -> None:
    binary, _ = setup_fake_runtime(tmp_path, monkeypatch, version_output=version_output)

    with pytest.raises(CadProductionError) as error:
        runtime.inspect_freecad_runtime(binary)

    assert error.value.code == "unsupported_freecad_build"
    assert str(error.value) == "unsupported_freecad_build"


def test_inspect_rejects_executable_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = setup_fake_runtime(tmp_path, monkeypatch, expected_hash="0" * 64)

    with pytest.raises(CadProductionError) as error:
        runtime.inspect_freecad_runtime(binary)

    assert error.value.code == "unsupported_freecad_build"


def test_inspect_rejects_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(CadProductionError) as error:
        runtime.inspect_freecad_runtime(tmp_path / "FreeCADCmd.exe")

    assert error.value.code == "freecad_unavailable"


def test_inspect_rejects_symlink_before_resolving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = setup_fake_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == binary)

    with pytest.raises(CadProductionError) as error:
        runtime.inspect_freecad_runtime(binary)

    assert error.value.code == "unsupported_freecad_build"


def test_inspect_maps_process_start_failure_to_stable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = setup_fake_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private path")),
    )

    with pytest.raises(CadProductionError) as error:
        runtime.inspect_freecad_runtime(binary)

    assert error.value.code == "freecad_unavailable"
    assert "private path" not in str(error.value)


def test_inspect_uses_only_runtime_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, calls = setup_fake_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("PRIVATE_API_TOKEN", "must-not-be-forwarded")

    runtime.inspect_freecad_runtime(binary)

    env = calls[0]["env"]
    assert "PRIVATE_API_TOKEN" not in env
    assert calls[0]["stdin"] is subprocess.DEVNULL
