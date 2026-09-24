from book_production_platform.graphics_production.telemetry import BatchTelemetry


def test_batch_telemetry_has_zero_model_calls_by_default() -> None:
    telemetry = BatchTelemetry(fresh_count=3, replay_count=1, bytes_written=42)

    assert telemetry.model_calls == 0
    assert telemetry.to_dict() == {
        "model_calls": 0,
        "fresh_count": 3,
        "replay_count": 1,
        "bytes_written": 42,
    }
