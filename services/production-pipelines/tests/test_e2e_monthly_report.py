"""Real end-to-end monthly report run: all four stages as real subprocesses.

Small, bounded synthetic dataset; every stage CLI runs exactly as its owner
documents it. Skipped when the stage service dependencies are missing on the
host.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from conftest import PIPELINE_DEPENDENCIES_AVAILABLE, STAGE_ROOTS_AVAILABLE
from synthetic_fixtures import write_config

from production_pipelines.cli import main
from production_pipelines.contracts import parse_manifest

pytestmark = [
    pytest.mark.skipif(not PIPELINE_DEPENDENCIES_AVAILABLE, reason="stage service python dependencies missing"),
    pytest.mark.skipif(not STAGE_ROOTS_AVAILABLE, reason="stage service roots missing"),
]


def test_monthly_report_runs_end_to_end_with_real_stage_clis(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    output = tmp_path / "run"

    exit_code = main(["monthly_report", "--config", str(config), "--output", str(output)])

    assert exit_code == 0
    manifest_path = output / "pipeline-manifest.json"
    assert manifest_path.is_file()
    manifest = parse_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert [record.stage for record in manifest.stages] == ["data_pipeline", "data_viz", "spreadsheet", "documents"]
    assert manifest.model_calls == 0

    # The chain artifacts exist at their recorded relative paths.
    for record in manifest.stages:
        for artifact in record.artifacts:
            artifact_path = output / artifact.path
            assert artifact_path.is_file(), artifact.path
            assert artifact.byte_size == artifact_path.stat().st_size

    # The rendered report document references the chart, workbook and Thai totals.
    documents_stage = manifest.stages[3]
    docx_path = next(entry for entry in documents_stage.artifacts if entry.name == "document.docx")
    with ZipFile(output / docx_path.path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    text = " ".join(element.text or "" for element in root.iter() if element.tag.endswith("}t"))
    assert "รายงานรายเดือน กันยายน 2026" in text
    assert "chart.svg" in text
    assert "workbook.xlsx" in text
    assert "2971.25" in text


def test_monthly_report_refuses_existing_output(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    output = tmp_path / "run"
    output.mkdir()
    exit_code = main(["monthly_report", "--config", str(config), "--output", str(output)])
    assert exit_code == 5
