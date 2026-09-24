"""JSON CLI for the Audio Master V1A product."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .jobs import master_audio
from .presets import MAX_PROCESS_TIMEOUT_SECONDS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audio-production")
    commands = parser.add_subparsers(dest="command", required=True)
    master = commands.add_parser("master", help="master a WAV with an allowlisted AudioIR preset")
    master.add_argument("--audio-ir", required=True)
    master.add_argument("--input-root", required=True)
    master.add_argument("--output-root", required=True)
    master.add_argument("--ffmpeg")
    master.add_argument("--timeout-s", type=int, default=MAX_PROCESS_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = Path(args.audio_ir)
    try:
        if path.is_symlink():
            raise OSError("AudioIR is unavailable or exceeds its size limit")
        with path.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise OSError("AudioIR is unavailable or exceeds its size limit")
        payload: Any = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"status": "rejected", "error": "invalid_audio_ir_file"}, sort_keys=True))
        return 2
    result = master_audio(payload, args.input_root, args.output_root, args.ffmpeg, timeout_s=args.timeout_s)
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
