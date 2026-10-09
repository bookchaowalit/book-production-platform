"""End-to-end pipeline: synthetic transcript artifact to rendered documents.

Runs the local chain ``transcript artifact -> SRT + transcript-document-request``
entirely in-process, then renders the emitted request through the sibling
``document-production`` CLI as a real subprocess. The document is the
deterministic verbatim transcript (no AI summary; that is deferred to T8).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from synthetic_fixtures import synthetic_artifact_payload

from video_postpipeline.cli import main

REPO_ROOT = Path(__file__).parents[3]
DOCUMENTS_SERVICE_ROOT = REPO_ROOT / "services" / "document-production-api"
DOCUMENTS_IR_ROOT = REPO_ROOT / "packages" / "document-production-ir"

DOCUMENTS_DEPENDENCIES_AVAILABLE = True
try:
    import docx  # noqa: F401
    import reportlab  # noqa: F401
except ImportError:  # pragma: no cover - only on hosts without the documents service deps
    DOCUMENTS_DEPENDENCIES_AVAILABLE = False


def test_transcript_artifact_flows_to_srt_and_rendered_document(tmp_path: Path):
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(synthetic_artifact_payload(), ensure_ascii=False), encoding="utf-8")

    subtitles_dir = tmp_path / "subtitles"
    request_path = tmp_path / "transcript-document-request.json"

    assert main(["subtitles", "--input", str(artifact_path), "--output-dir", str(subtitles_dir), "--format", "srt"]) == 0
    assert main(
        [
            "transcript-request",
            "--input", str(artifact_path),
            "--output", str(request_path),
            "--issue-date", "2026-09-26",
            "--title", "บทถอดเสียง การทดสอบ",
        ]
    ) == 0

    srt = (subtitles_dir / "demo-001.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "สวัสดีครับ" in srt

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "transcript-document-request.v1"

    if not DOCUMENTS_DEPENDENCIES_AVAILABLE:  # pragma: no cover - defensive skip
        pytest.skip("document-production dependencies are not installed")

    output_dir = tmp_path / "documents-out"
    env = dict(os.environ)
    python_path = os.pathsep.join(
        part for part in [env.get("PYTHONPATH"), str(DOCUMENTS_SERVICE_ROOT), str(DOCUMENTS_IR_ROOT)] if part
    )
    env["PYTHONPATH"] = python_path
    result = subprocess.run(
        [
            sys.executable, "-m", "document_production.cli", "transcript",
            "--input", str(request_path),
            "--output", str(output_dir),
            "--idempotency-key", "e2e-transcript-001",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        shell=False,
        check=False,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"documents CLI failed: {result.stderr}\n{result.stdout}"

    manifests = list(output_dir.rglob("manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "accepted"
    assert manifest["model_calls"] == 0
    assert manifest["qa"]["status"] == "passed"
    assert manifest["recipe"] == "transcript-v1"
    assert {artifact["filename"] for artifact in manifest["artifacts"]} == {"document.docx", "document.pdf"}
    job_dir = manifests[0].parent
    assert (job_dir / "document.docx").stat().st_size > 0
    assert (job_dir / "document.pdf").read_bytes()[:5] == b"%PDF-"
