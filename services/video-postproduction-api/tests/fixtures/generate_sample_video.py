"""Generate a short deterministic MP4 fixture without shell interpolation."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def generate_sample_video(output_path: str | Path, executable: str | None = None) -> Path:
    """Create a two-second test-pattern video with a sine-wave audio stream."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = executable or shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required to generate the sample video")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg could not generate the sample video")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate_sample_video(args.output)


if __name__ == "__main__":
    main()
