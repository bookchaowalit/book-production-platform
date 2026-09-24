"""Create a synthetic, modulated tone fixture for Audio Master QA."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


def generate(path: Path, duration_seconds: int = 3, sample_rate: int = 44_100) -> None:
    frames = bytearray()
    for index in range(duration_seconds * sample_rate):
        time_s = index / sample_rate
        envelope = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * 2.2 * time_s))
        signal = envelope * (0.11 * math.sin(2 * math.pi * 173 * time_s) + 0.06 * math.sin(2 * math.pi * 281 * time_s))
        frames.extend(struct.pack("<h", round(signal * 32767)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.output)
    print(f"created synthetic PCM WAV: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
