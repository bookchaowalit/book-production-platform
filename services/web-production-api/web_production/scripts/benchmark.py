"""Run the deterministic 20-page Web V1A benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..jobs import run_page_job


def run_benchmark(fixture_path: str | Path, output_dir: str | Path) -> dict[str, int]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    pages = fixture.get("pages") if isinstance(fixture, dict) else None
    if not isinstance(pages, list) or not pages or not all(isinstance(item, dict) for item in pages):
        raise ValueError("fixture must contain a non-empty pages array")
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    accepted = rejected = model_calls = replay_hits = qa_failures = 0
    for index, page in enumerate(pages):
        result = run_page_job(page, output, f"benchmark-{index + 1}")
        if result.status == "accepted":
            accepted += 1
        else:
            rejected += 1
        model_calls += result.model_calls
        if not result.artifacts:
            qa_failures += 1
        replay = run_page_job(page, output, f"benchmark-{index + 1}")
        replay_hits += int(replay.replayed)
        model_calls += replay.model_calls
    return {"total": len(pages), "accepted": accepted, "rejected": rejected, "model_calls": model_calls, "replay_hits": replay_hits, "qa_failures": qa_failures}


def main() -> int:
    parser = argparse.ArgumentParser(prog="web-benchmark")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.fixture, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
