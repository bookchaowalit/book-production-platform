"""Local JSON command line interface for Data Pipeline Builder V1A."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from book_production_platform.data_pipeline.jobs import (
    PipelineJobError,
    run_pipeline_job,
)
from book_production_platform.data_pipeline_ir.models import PipelineRequest
from book_production_platform.data_pipeline_ir.validation import (
    MAX_PIPELINE_REQUEST_BYTES,
)


class PipelineInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book-production data")
    commands = parser.add_subparsers(dest="command", required=True)
    pipeline = commands.add_parser("pipeline", help="Data SQL/Pipeline Builder V1A operations")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    build = pipeline_commands.add_parser("build", help="compile and locally plan a typed pipeline")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--output-root", required=True, type=Path)
    return parser


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.input.is_symlink():
            raise PipelineInputError("cli_io_error")
        with args.input.open("rb") as source:
            body = source.read(MAX_PIPELINE_REQUEST_BYTES + 1)
    except PipelineInputError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    except OSError:
        _emit({"status": "rejected", "error": "cli_io_error"})
        return 2

    if len(body) > MAX_PIPELINE_REQUEST_BYTES:
        _emit({"status": "rejected", "error": "pipeline_request_too_large"})
        return 2
    try:
        request = PipelineRequest.model_validate_json(body)
    except ValidationError:
        _emit({"status": "rejected", "error": "invalid_pipeline_request"})
        return 2

    try:
        result = run_pipeline_job(request, args.output_root)
    except PipelineJobError as error:
        _emit({"status": "rejected", "error": error.code})
        return 2
    _emit(result.to_dict())
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
