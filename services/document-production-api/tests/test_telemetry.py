from document_production.telemetry import build_cost_event


def test_cost_event_keeps_local_compute_cost_unknown_instead_of_zero():
    event = build_cost_event(
        job_id="job-1",
        model_calls=0,
        cpu_seconds=1.25,
        bytes_written=500,
        status="accepted",
    )
    assert event.model_calls == 0
    assert event.cost_status == "unknown"
    assert event.estimated_cost_usd is None
    assert event.rate_card_version == "local-v1"


def test_cost_event_separates_wall_and_cpu_time_with_attempt_metadata():
    event = build_cost_event(
        job_id="job-1",
        attempt_id="attempt-1",
        model_calls=0,
        wall_seconds=5.0,
        cpu_seconds=1.0,
        bytes_written=500,
        status="accepted",
    )
    payload = event.to_dict()
    assert payload["schema_version"] == "cost-event-v2"
    assert payload["attempt_id"] == "attempt-1"
    assert payload["wall_seconds"] == 5.0
    assert payload["cpu_seconds"] == 1.0
    assert payload["replayed"] is False
