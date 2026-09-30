"""Validation and evidence models for Unity-produced GameIR artifacts."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_production_platform.game_ir import GameIR
from book_production_platform.game_production.errors import GameJobError

MAX_WORKER_REPORT_BYTES = 64 * 1024
MAX_GAMEPLAY_TRACE_BYTES = 256 * 1024
MAX_TEST_XML_BYTES = 8 * 1024 * 1024
WORKER_REPORT_FIELDS = frozenset(
    {
        "status",
        "unity_version",
        "scene_path",
        "player_count",
        "wall_collider_count",
        "objective_count",
        "exit_count",
        "unique_entity_ids",
        "external_resource_count",
        "disallowed_component_count",
        "trusted_template_no_network_references",
        "music_source_count",
        "collect_sound_configured",
    }
)


@dataclass(frozen=True)
class TestRunSummary:
    total: int
    passed: int


@dataclass(frozen=True)
class UnityQaEvidence:
    unity_version: str
    unity_executable_sha256: str
    worker_report: dict[str, Any]
    editmode: TestRunSummary
    playmode: TestRunSummary
    gameplay_events: tuple[str, ...]
    gameplay_trace_sha256: str
    elapsed_seconds: float
    total_log_bytes: int


def verify_worker_report(project_path: Path, game_ir: GameIR, unity_version: str) -> dict[str, Any]:
    """Require Unity's scene hierarchy report to match the validated GameIR."""
    report_path = project_path / ".book-production" / "worker-report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise GameJobError("scene_qa_failed", "Unity worker report is unavailable")
    try:
        if report_path.stat().st_size > MAX_WORKER_REPORT_BYTES:
            raise GameJobError("scene_qa_failed", "Unity worker report exceeds its size limit")
        with report_path.open("rb") as source:
            raw = source.read(MAX_WORKER_REPORT_BYTES + 1)
        if len(raw) > MAX_WORKER_REPORT_BYTES:
            raise GameJobError("scene_qa_failed", "Unity worker report exceeds its size limit")
        report = json.loads(raw.decode("utf-8"))
    except GameJobError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GameJobError("scene_qa_failed", "Unity worker report is invalid") from error
    audio = game_ir.audio
    expected_walls = sum(len(wall.cells) for wall in game_ir.walls)
    expected_scene = f"Assets/Scenes/{game_ir.scene_id}.unity"
    if (
        not isinstance(report, dict)
        or set(report) != WORKER_REPORT_FIELDS
        or report.get("status") != "verified"
        or report.get("unity_version") != unity_version
        or report.get("scene_path") != expected_scene
        or report.get("player_count") != 1
        or report.get("wall_collider_count") != expected_walls
        or report.get("objective_count") != len(game_ir.objectives)
        or report.get("exit_count") != 1
        or report.get("unique_entity_ids") is not True
        or report.get("external_resource_count") != 0
        or report.get("disallowed_component_count") != 0
        or report.get("trusted_template_no_network_references") is not True
        or report.get("music_source_count") != (1 if audio and audio.music else 0)
        or report.get("collect_sound_configured") is not bool(audio and audio.collect_sfx)
    ):
        raise GameJobError("scene_qa_failed", "Unity scene verification did not match the GameIR")
    return report


def verify_gameplay_trace(trace_path: Path, game_ir: GameIR) -> tuple[tuple[str, ...], bytes]:
    """Require one deterministic collection/win trace for every objective."""
    if trace_path.is_symlink() or not trace_path.is_file():
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace is missing")
    try:
        if trace_path.stat().st_size > MAX_GAMEPLAY_TRACE_BYTES:
            raise GameJobError("gameplay_qa_failed", "Unity gameplay trace exceeds its size limit")
        with trace_path.open("rb") as source:
            raw = source.read(MAX_GAMEPLAY_TRACE_BYTES + 1)
        if len(raw) > MAX_GAMEPLAY_TRACE_BYTES:
            raise GameJobError("gameplay_qa_failed", "Unity gameplay trace exceeds its size limit")
        document = json.loads(raw.decode("utf-8"))
    except GameJobError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace is invalid") from error
    if not isinstance(document, dict) or set(document) != {"events"}:
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace has an invalid shape")
    values = document["events"]
    if not isinstance(values, list) or not values or len(values) > 512:
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace has an invalid event list")
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in values):
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace contains an invalid event")

    events = tuple(values)
    blocked_event = f"exit_blocked:{game_ir.exit.id}"
    won_event = f"game_won:{game_ir.exit.id}"
    objective_events = Counter(
        event for event in events if event.startswith("objective_collected:")
    )
    expected_objective_events = Counter(
        f"objective_collected:{objective.id}" for objective in game_ir.objectives
    )
    allowed_events = {
        "player_moved",
        blocked_event,
        won_event,
        *expected_objective_events.keys(),
    }
    if (
        events[0] != blocked_event
        or events.count("player_moved") != 1
        or objective_events != expected_objective_events
        or events.count(won_event) != 1
        or events[-1] != won_event
        or any(event not in allowed_events for event in events)
    ):
        raise GameJobError("gameplay_qa_failed", "Unity gameplay trace did not satisfy the collection loop")
    last_objective = max(
        index for index, event in enumerate(events) if event.startswith("objective_collected:")
    )
    if events.index(won_event) <= last_objective:
        raise GameJobError("gameplay_qa_failed", "Unity reported victory before collecting every objective")
    return events, raw


def summarize_test_xml(test_xml: Path) -> TestRunSummary:
    """Return counts only for non-empty Unity test runs with every case passed."""
    if test_xml.is_symlink() or not test_xml.is_file():
        raise GameJobError("unity_test_failed", "Unity test result is missing")
    try:
        if test_xml.stat().st_size > MAX_TEST_XML_BYTES:
            raise GameJobError("unity_test_failed", "Unity test result exceeds its size limit")
        with test_xml.open("rb") as source:
            raw = source.read(MAX_TEST_XML_BYTES + 1)
        if len(raw) > MAX_TEST_XML_BYTES:
            raise GameJobError("unity_test_failed", "Unity test result exceeds its size limit")
        root = ET.fromstring(raw)
        cases = list(root.iter("test-case"))
        total = int(root.attrib.get("total", root.attrib.get("testcasecount", "0")))
        passed = int(root.attrib.get("passed", "0"))
        failed = int(root.attrib.get("failed", "0"))
        skipped = int(root.attrib.get("skipped", "0"))
        inconclusive = int(root.attrib.get("inconclusive", "0"))
        not_run = int(root.attrib.get("not-run", "0"))
    except GameJobError:
        raise
    except (OSError, ET.ParseError, ValueError, RecursionError) as error:
        raise GameJobError("unity_test_failed", "Unity test result is invalid") from error
    if (
        root.tag != "test-run"
        or root.attrib.get("result") != "Passed"
        or total < 1
        or passed != total
        or failed != 0
        or skipped != 0
        or inconclusive != 0
        or not_run != 0
        or len(cases) != total
        or any(case.attrib.get("result") != "Passed" or case.attrib.get("runstate") != "Runnable" for case in cases)
    ):
        raise GameJobError("unity_test_failed", "Unity tests failed, were skipped, or did not run")
    return TestRunSummary(total=total, passed=passed)
