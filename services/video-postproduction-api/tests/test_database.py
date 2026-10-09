from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from video_postproduction.models import EDL, Project
except ImportError:
    EDL = None
    Project = None

from .conftest import valid_initial_edl


def test_project_round_trips_with_initial_edl(session):
    if session is None or Project is None or EDL is None:
        pytest.fail("video_postproduction database models are not implemented")

    project = Project(name="demo", status="created")
    session.add(project)
    session.flush()
    edl = EDL(
        project_id=project.id,
        schema_version="edl.v1",
        document=valid_initial_edl(project.id),
    )
    session.add(edl)
    session.commit()

    assert session.get(Project, project.id).name == "demo"
    assert session.get(EDL, edl.id).document["version"] == 1
