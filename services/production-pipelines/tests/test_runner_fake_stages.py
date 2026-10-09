"""Unit tests for the monthly report runner with fake stage CLIs.

The fake invoker mimics the real stage CLI contract: it parses the same
argv flags, writes the same job/manifest/artifact layout, and prints the
same JSON result shapes — without spawning subprocesses.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from synthetic_fixtures import sha256_of, synthetic_csv_text, write_config

from production_pipelines.contracts import CONTRACT_MANIFEST, parse_manifest
from production_pipelines.runner import (
    EXIT_CONTRACT_VIOLATION,
    EXIT_OK,
    EXIT_OUTPUT_EXISTS,
    EXIT_STAGE_FAILED,
    run_monthly_report,
)

uuid_sequence = iter(range(1, 1000))


def _next_uuid() -> str:
    return f"00000000-0000-0000-0000-{next(uuid_sequence):012d}"


def _job_result(status: str, job_id: str, artifacts: list[dict[str, Any]], error_code: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "job_id": job_id,
        "artifacts": artifacts,
        "error_code": error_code,
    }
    return payload


def _write_job_artifacts(output_dir: Path, files: dict[str, bytes]) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for name, data in files.items():
        (output_dir / name).write_bytes(data)
        artifacts.append(
            {
                "filename": name,
                "media_type": "application/octet-stream",
                "content_hash": sha256_of(data),
                "byte_size": len(data),
            }
        )
    return artifacts


class FakeStageCli:
    """Fake of the three stage CLIs with realistic layouts."""

    def __init__(
        self,
        fail_stage: str | None = None,
        reject_stage: str | None = None,
        corrupt_cleaned_csv: bool = False,
    ) -> None:
        self.fail_stage = fail_stage
        self.reject_stage = reject_stage
        self.corrupt_cleaned_csv = corrupt_cleaned_csv
        self.requests: dict[str, dict[str, Any]] = {}

    def __call__(self, command: Any) -> Any:
        from production_pipelines.stages import CompletedStage

        argv = command.argv
        stage = command.stage
        if stage == self.fail_stage:
            return CompletedStage(2, "", "simulated stage crash")
        if stage == self.reject_stage:
            return CompletedStage(2, json.dumps(_job_result("rejected", "", [], error_code="invalid_request")), "")

        options = self._options(argv)
        request = json.loads((command.cwd / options["--input"]).read_text(encoding="utf-8"))
        self.requests[stage] = request
        output_root = command.cwd / options["--output"]

        if stage in {"data_pipeline", "data_viz"}:
            job_dir = output_root / self._job_hash(options["--idempotency-key"])
            csv_text = "x,y\nA,10\nB,20\n" if self.corrupt_cleaned_csv else "category,amount\nA,10\nB,20\n"
            files = {"cleaned.csv": csv_text.encode("utf-8")}
            if stage == "data_viz":
                files["report.json"] = json.dumps(
                    {
                        "schema_version": "data-report.v1",
                        "chart": {
                            "categories": ["A", "B"],
                            "values": ["10", "20"],
                            "aggregate_total": "30",
                        },
                    }
                ).encode("utf-8")
                files["chart.svg"] = b"<svg xmlns='x'></svg>"
            else:
                files["report.json"] = json.dumps(
                    {
                        "schema_version": "data-report.v1",
                        "columns": [
                            {"key": "category", "label": "Category", "data_type": "text", "required": False},
                            {"key": "amount", "label": "Amount", "data_type": "decimal", "required": False},
                        ],
                    }
                ).encode("utf-8")
            artifacts = _write_job_artifacts(job_dir, files)
            manifest = {"status": "accepted", "job_id": "data-job-1", "artifacts": artifacts, "error_code": None}
            self._write_manifest(job_dir, manifest, options["--idempotency-key"])
            return CompletedStage(0, json.dumps(manifest), "")
        if stage == "spreadsheet":
            job_dir = output_root / self._job_hash(options["--idempotency-key"])
            artifacts = _write_job_artifacts(job_dir, {"workbook.xlsx": b"PK\x03\x04fake-xlsx"})
            manifest = {"status": "accepted", "job_id": "wb-job-1", "artifacts": artifacts, "error_code": None}
            self._write_manifest(job_dir, manifest, options["--idempotency-key"])
            return CompletedStage(0, json.dumps(manifest), "")
        # documents
        job_dir = output_root / "jobs" / _next_uuid()
        artifacts = _write_job_artifacts(job_dir, {"document.docx": b"PK\x03\x04fake-docx", "document.pdf": b"%PDF-fake"})
        manifest = {"status": "accepted", "job_id": job_dir.name, "artifacts": artifacts, "error_code": None}
        self._write_manifest(job_dir, manifest, options["--idempotency-key"])
        return CompletedStage(0, json.dumps(manifest), "")

    @staticmethod
    def _job_hash(idempotency_key: str) -> str:
        return f"job-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}"

    @staticmethod
    def _write_manifest(job_dir: Path, manifest: dict[str, Any], idempotency_key: str) -> None:
        payload = dict(manifest)
        payload["idempotency_key"] = idempotency_key
        (job_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _options(argv: list[str]) -> dict[str, str]:
        options: dict[str, str] = {}
        for index, arg in enumerate(argv):
            if arg.startswith("--") and index + 1 < len(argv):
                options[arg] = argv[index + 1]
        return options


class TamperingFake(FakeStageCli):
    """Fake that writes a manifest whose recorded hash does not match the file."""

    def __init__(self, tamper_stage: str) -> None:
        super().__init__()
        self.tamper_stage = tamper_stage

    def __call__(self, command: Any) -> Any:
        from production_pipelines.stages import CompletedStage

        if command.stage == self.tamper_stage:
            options = self._options(command.argv)
            output_root = command.cwd / options["--output"]
            job_dir = output_root / self._job_hash(options["--idempotency-key"])
            artifacts = _write_job_artifacts(job_dir, {"cleaned.csv": b"tampered"})
            manifest = dict(artifacts[0])
            manifest["content_hash"] = "0" * 64
            payload = {"status": "accepted", "job_id": "data-job-1", "artifacts": [manifest], "error_code": None}
            self._write_manifest(job_dir, payload, options["--idempotency-key"])
            return CompletedStage(0, json.dumps(payload), "")
        return super().__call__(command)


def test_happy_path_publishes_validated_manifest(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli()
    output = tmp_path / "run"

    result = run_monthly_report(config, output, invoker=fake)

    assert result.exit_code == EXIT_OK
    manifest_path = output / "pipeline-manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parse_manifest(manifest)  # published manifest validates against pipeline-report.v1
    assert [record["stage"] for record in manifest["stages"]] == ["data_pipeline", "data_viz", "spreadsheet", "documents"]
    assert all(record["status"] == "accepted" for record in manifest["stages"])
    assert all(not Path(record["manifest_path"]).is_absolute() for record in manifest["stages"])
    # The viz request embeds the cleaned CSV text of stage 1
    assert "category,amount" in fake.requests["data_viz"]["csv_text"]
    # The document request carries the chart + workbook references
    document_request = json.loads((output / "stages/04-document/request.json").read_text(encoding="utf-8"))
    assert document_request["schema_version"] == "report-document-request.v1"
    assert document_request["charts"][0]["name"] == "chart.svg"
    assert document_request["workbook"]["name"] == "workbook.xlsx"


def test_stage_failure_fails_closed(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli(fail_stage="data_viz")
    output = tmp_path / "run"

    result = run_monthly_report(config, output, invoker=fake)

    assert result.exit_code == EXIT_STAGE_FAILED
    assert result.error is not None
    assert result.error["stage"] == "data_viz"
    assert result.error["error"] == "stage_exit_2"
    assert not output.exists()
    assert not list(tmp_path.glob(".run.staging-*"))


def test_stage_rejection_surfaces_stable_code(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli(reject_stage="spreadsheet")
    output = tmp_path / "run"

    result = run_monthly_report(config, output, invoker=fake)

    assert result.exit_code == EXIT_STAGE_FAILED
    assert result.error is not None
    assert result.error["stage"] == "spreadsheet"
    assert result.error["error"] == "invalid_request"
    assert not output.exists()


def test_artifact_hash_mismatch_fails_closed(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    fake = TamperingFake("data_pipeline")
    output = tmp_path / "run"

    result = run_monthly_report(config, output, invoker=fake)

    assert result.exit_code == EXIT_STAGE_FAILED
    assert result.error is not None
    assert result.error["error"] == "artifact_integrity_failed"
    assert not output.exists()


def test_config_rejecting_duplicate_workbook_keys_is_input_error(tmp_path: Path):
    config = write_config(tmp_path / "config.json", {"workbook": {"column_keys": {"category": "same", "amount": "same", "sold_on": "date"}}})
    fake = FakeStageCli()
    result = run_monthly_report(config, tmp_path / "run", invoker=fake)
    assert result.exit_code == 2
    assert not (tmp_path / "run").exists()


def test_handoff_contract_violation_is_exit_4(tmp_path: Path):
    """Cleaned CSV headers inconsistent with the stage report fail closed as a contract violation."""

    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli(corrupt_cleaned_csv=True)
    result = run_monthly_report(config, tmp_path / "run", invoker=fake)
    assert result.exit_code == EXIT_CONTRACT_VIOLATION
    assert result.error is not None
    assert result.error["error"] == "contract_violation"
    assert not (tmp_path / "run").exists()


def test_rerun_refuses_to_overwrite_existing_output(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli()
    output = tmp_path / "run"

    first = run_monthly_report(config, output, invoker=fake)
    assert first.exit_code == EXIT_OK
    manifest_before = (output / "pipeline-manifest.json").read_bytes()

    second = run_monthly_report(config, output, invoker=fake)
    assert second.exit_code == EXIT_OUTPUT_EXISTS
    assert (output / "pipeline-manifest.json").read_bytes() == manifest_before


def test_missing_config_is_input_error(tmp_path: Path):
    result = run_monthly_report(tmp_path / "missing.json", tmp_path / "run", invoker=FakeStageCli())
    assert result.exit_code == 2
    assert not (tmp_path / "run").exists()


def test_invalid_config_is_input_error(tmp_path: Path):
    config = write_config(tmp_path / "config.json")
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["chart"]["aggregation"] = "median"
    config.write_text(json.dumps(payload), encoding="utf-8")
    result = run_monthly_report(config, tmp_path / "run", invoker=FakeStageCli())
    assert result.exit_code == 2
    assert not (tmp_path / "run").exists()


def test_published_manifest_is_self_consistent(tmp_path: Path):
    """The synthetic csv chain keeps artifact bytes consistent with the manifest."""

    config = write_config(tmp_path / "config.json")
    fake = FakeStageCli()
    output = tmp_path / "run"
    assert run_monthly_report(config, output, invoker=fake).exit_code == EXIT_OK

    manifest = json.loads((output / "pipeline-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == CONTRACT_MANIFEST
    for record in manifest["stages"]:
        for artifact in record["artifacts"]:
            data = (output / artifact["path"]).read_bytes()
            assert sha256_of(data) == artifact["content_hash"]
            assert len(data) == artifact["byte_size"]
    # the synthetic fixture csv passed through the fake stages unchanged in spirit
    assert synthetic_csv_text().startswith("หมวด")
