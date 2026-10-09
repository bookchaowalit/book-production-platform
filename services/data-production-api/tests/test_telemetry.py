from data_production.telemetry import JobTelemetry


def test_telemetry_is_explicit_about_model_calls_and_replay() -> None:
    assert JobTelemetry(model_calls=0, bytes_written=12, replayed=False).to_dict() == {"model_calls": 0, "bytes_written": 12, "replayed": False}
