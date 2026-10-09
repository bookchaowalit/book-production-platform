from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from web_production.jobs import IdempotencyConflict, ReplayIntegrityError, run_page_job


def _page() -> dict:
    return {
        "page_id": "job-01",
        "slug": "job-page",
        "title": "Job page",
        "description": "A test page",
        "hero_heading": "Hello",
        "hero_body": "Body",
        "hero_cta": {"label": "Start", "href": "#signup"},
        "features": [{"title": "One", "body": "First"}],
    }


def test_job_writes_html_css_manifest_and_zero_model_calls(tmp_path: Path) -> None:
    result = run_page_job(_page(), tmp_path / "out", "key-1")

    assert result.status == "accepted"
    assert result.replayed is False
    assert result.model_calls == 0
    assert {artifact.media_type for artifact in result.artifacts} == {"text/html", "text/css"}
    assert (tmp_path / "out" / result.manifest_filename).exists()


def test_replay_returns_same_artifacts_without_writes(tmp_path: Path) -> None:
    first = run_page_job(_page(), tmp_path / "out", "key-1")

    replay = run_page_job(_page(), tmp_path / "out", "key-1")

    assert replay.replayed is True
    assert replay.artifacts == first.artifacts
    assert replay.bytes_written == 0


def test_changed_input_conflicts(tmp_path: Path) -> None:
    run_page_job(_page(), tmp_path / "out", "key-1")
    changed = {**_page(), "title": "changed"}

    with pytest.raises(IdempotencyConflict):
        run_page_job(changed, tmp_path / "out", "key-1")


def test_tampered_html_is_rejected_on_replay(tmp_path: Path) -> None:
    first = run_page_job(_page(), tmp_path / "out", "key-1")
    html = next(artifact for artifact in first.artifacts if artifact.media_type == "text/html")
    html_path = tmp_path / "out" / html.filename
    html_path.write_text(html_path.read_text(encoding="utf-8").replace("Job page", "Tampered"), encoding="utf-8")

    with pytest.raises(ReplayIntegrityError):
        run_page_job(_page(), tmp_path / "out", "key-1")


def test_duplicate_threads_share_one_package(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_page_job(_page(), tmp_path / "out", "key-1"), range(2)))

    assert {result.status for result in results} == {"accepted"}
    assert sum(result.replayed for result in results) == 1
