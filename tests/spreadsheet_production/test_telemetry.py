from book_production_platform.spreadsheet_production.telemetry import JobTelemetry


def test_job_telemetry_defaults_to_zero_model_calls() -> None:
    telemetry = JobTelemetry(replayed=True, bytes_written=0)

    assert telemetry.to_dict() == {"model_calls": 0, "replayed": True, "bytes_written": 0}
