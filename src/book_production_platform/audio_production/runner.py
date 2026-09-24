"""Bounded subprocess execution for the local FFmpeg adapter."""

from __future__ import annotations

import math
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from .errors import AudioProductionError
from .presets import MAX_PROCESS_OUTPUT_BYTES


@dataclass(frozen=True)
class ProcessOutput:
    return_code: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    truncated: bool


@dataclass
class _Capture:
    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stored: int = 0
    truncated: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def drain(self, pipe: object, target: bytearray) -> None:
        try:
            while True:
                chunk = pipe.read(64 * 1024)  # type: ignore[attr-defined]
                if not chunk:
                    break
                with self.lock:
                    available = max(0, self.limit - self.stored)
                    kept = chunk[:available]
                    target.extend(kept)
                    self.stored += len(kept)
                    if len(kept) != len(chunk):
                        self.truncated = True
        finally:
            pipe.close()  # type: ignore[attr-defined]


def run_process(argv: Sequence[str], timeout_seconds: float) -> ProcessOutput:
    """Run an argument vector without a shell and cap combined process logs."""
    if not argv or any(not isinstance(argument, str) for argument in argv) or not argv[0].strip():
        raise AudioProductionError("invalid_command", "FFmpeg command is invalid")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise AudioProductionError("deadline_exceeded", "Audio processing deadline was exceeded")
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except FileNotFoundError as error:
        raise AudioProductionError("ffmpeg_unavailable", "FFmpeg executable is unavailable") from error
    except OSError as error:
        raise AudioProductionError("ffmpeg_start_failed", "FFmpeg process could not be started") from error

    assert process.stdout is not None and process.stderr is not None
    capture = _Capture(MAX_PROCESS_OUTPUT_BYTES)
    readers = (
        threading.Thread(target=capture.drain, args=(process.stdout, capture.stdout), daemon=True),
        threading.Thread(target=capture.drain, args=(process.stderr, capture.stderr), daemon=True),
    )
    for reader in readers:
        reader.start()

    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=2)
        raise AudioProductionError("deadline_exceeded", "FFmpeg exceeded the audio job deadline") from error
    for reader in readers:
        reader.join(timeout=2)
    elapsed = max(0.0, time.monotonic() - start)
    if any(reader.is_alive() for reader in readers):
        raise AudioProductionError("ffmpeg_output_unavailable", "FFmpeg output could not be collected")
    return ProcessOutput(return_code, bytes(capture.stdout), bytes(capture.stderr), elapsed, capture.truncated)
