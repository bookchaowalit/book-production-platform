from __future__ import annotations

from uuid import uuid4

from video_postproduction.jobs.queue import CeleryJobQueue, InlineJobQueue, enqueue_asset_pipeline
from video_postproduction.models import MediaAsset, ProcessingJob, Project


class FakeCeleryApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def send_task(self, name: str, *, args: list[str]) -> None:
        self.calls.append((name, args))


def test_celery_queue_sends_only_job_and_stage_ids():
    app = FakeCeleryApp()
    job_id = uuid4()

    CeleryJobQueue(app).enqueue(job_id, "probe")

    assert app.calls == [("video_postproduction.jobs.tasks.run_stage", [str(job_id), "probe"])]


def test_pipeline_is_idempotent_for_same_fingerprint(session):
    project = Project(name="demo", status="created")
    session.add(project)
    session.flush()
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/original/video.mp4",
        original_filename="video.mp4",
        media_type="video/mp4",
        size_bytes=10,
        sha256="a" * 64,
        metadata_json={},
        status="uploaded",
    )
    session.add(asset)
    session.commit()
    queue = InlineJobQueue()

    first = enqueue_asset_pipeline(session, queue, asset.id)
    second = enqueue_asset_pipeline(session, queue, asset.id)

    assert first.id == second.id
    assert session.query(ProcessingJob).count() == 1
    assert queue.calls == [(first.id, "probe")]
