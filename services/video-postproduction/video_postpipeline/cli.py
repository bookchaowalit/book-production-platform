"""CLI for the deterministic video post-production pipeline tools.

Run from the service directory (or with the service on ``PYTHONPATH``):

.. code-block:: text

    python -m video_postpipeline.cli --help

Subcommands: ``validate``, ``subtitles``, ``extract-audio``,
``replace-audio``, ``overlay``, ``transcript-request``. Exit codes: ``0`` success, ``2`` input or validation
error, ``4`` media or tool error (ffmpeg/ffprobe missing, failed, or timed
out). All subprocess calls use typed argv, ``shell=False`` and bounded
timeouts.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date

from .audio_extract import ExtractionError, extract_audio
from .audio_replace import replace_audio
from .contract import ValidationError, load_transcript_artifact
from .document_request import DocumentRequestError, write_document_request
from .overlay import POSITIONS, overlay_graphic
from .subtitles import write_subtitles

EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_MEDIA_ERROR = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-postproduction-pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a transcript artifact against transcript-artifact.v1")
    validate.add_argument("--input", required=True, help="path to a transcript artifact JSON file")

    subtitles = commands.add_parser("subtitles", help="emit SRT/VTT subtitle files from a transcript artifact")
    subtitles.add_argument("--input", required=True, help="path to a transcript artifact JSON file")
    subtitles.add_argument("--output-dir", required=True, help="directory for the subtitle files")
    subtitles.add_argument("--format", choices=["srt", "vtt", "both"], default="both", help="subtitle format to emit")

    extract = commands.add_parser("extract-audio", help="extract the audio track of a video for the audio master pipeline")
    extract.add_argument("--input", required=True, help="path to the source video file")
    extract.add_argument("--output-dir", required=True, help="directory for the audio file and batch manifest")
    extract.add_argument("--format", choices=["wav", "mp3"], default="wav", help="wav yields pcm_s24le plus an audio-batch.v1 manifest; mp3 is a preview without a manifest")
    extract.add_argument("--batch-id", default=None, help="audio-batch.v1 batch_id (defaults to the video stem)")
    extract.add_argument("--track-id", default=None, help="audio-batch.v1 track_id (defaults to the video stem)")
    extract.add_argument("--timeout-seconds", type=float, default=120.0, help="bounded timeout for each ffmpeg/ffprobe call")

    replace = commands.add_parser("replace-audio", help="replace a video's audio with a mastered WAV and publish video-remux.v1")
    replace.add_argument("--video", required=True, help="path to the source video file")
    replace.add_argument("--audio", required=True, help="path to the mastered audio file")
    replace.add_argument("--output-dir", required=True, help="directory for the remuxed MP4 and video-remux.json")
    replace.add_argument("--remux-id", default=None, help="output name (defaults to <video stem>-remastered)")
    replace.add_argument("--timeout-seconds", type=float, default=600.0, help="bounded timeout for each ffmpeg/ffprobe call")

    overlay = commands.add_parser("overlay", help="burn a graphic onto a video and publish video-overlay.v1")
    overlay.add_argument("--video", required=True, help="path to the source video file")
    overlay.add_argument("--image", required=True, help="path to the PNG/JPEG graphic")
    overlay.add_argument("--output-dir", required=True, help="directory for the MP4 and video-overlay.json")
    overlay.add_argument("--position", choices=sorted(POSITIONS), default="bottom-right")
    overlay.add_argument("--width-ratio", type=float, default=0.2, help="graphic width as a fraction of the video width")
    overlay.add_argument("--margin", type=int, default=24, help="margin in pixels from the chosen edges")
    overlay.add_argument("--overlay-id", default=None, help="output name (defaults to <video stem>-overlay)")
    overlay.add_argument("--timeout-seconds", type=float, default=1800.0)

    request = commands.add_parser("transcript-request", help="emit a transcript-document-request.v1 payload for the Documents service")
    request.add_argument("--input", required=True, help="path to a transcript artifact JSON file")
    request.add_argument("--output", required=True, help="path for the emitted request JSON file")
    request.add_argument("--issue-date", required=True, help="document issue date in YYYY-MM-DD form")
    request.add_argument("--document-id", default=None, help="document id (defaults to the transcript id)")
    request.add_argument("--title", default=None, help="document title (defaults to 'Transcript <transcript_id>')")
    request.add_argument("--locale", default=None, help="document locale (defaults from the artifact language)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            artifact = load_transcript_artifact(args.input)
            print(f"valid {artifact.schema_version}: {artifact.transcript_id} ({len(artifact.segments)} segments)")
            return EXIT_OK

        if args.command == "subtitles":
            artifact = load_transcript_artifact(args.input)
            formats = ("srt", "vtt") if args.format == "both" else (args.format,)
            written = write_subtitles(artifact, args.output_dir, formats=formats)
            for path in written:
                print(path)
            return EXIT_OK

        if args.command == "extract-audio":
            result = extract_audio(
                args.input,
                args.output_dir,
                audio_format=args.format,
                batch_id=args.batch_id,
                track_id=args.track_id,
                timeout_seconds=args.timeout_seconds,
            )
            print(result.audio_path)
            print(f"codec={result.codec_name}")
            print(f"duration_seconds={result.duration_seconds:.3f}")
            if result.manifest_path is None:
                print("audio-batch manifest: not published (mp3 is not covered by audio-batch.v1)")
            else:
                print(result.manifest_path)
            return EXIT_OK

        if args.command == "replace-audio":
            replaced = replace_audio(
                args.video, args.audio, args.output_dir,
                remux_id=args.remux_id, timeout_seconds=args.timeout_seconds,
            )
            print(replaced.video_path)
            print(f"duration_seconds={replaced.duration_seconds:.3f}")
            print(replaced.manifest_path)
            return EXIT_OK

        if args.command == "overlay":
            overlaid = overlay_graphic(
                args.video, args.image, args.output_dir, position=args.position, width_ratio=args.width_ratio,
                margin_px=args.margin, overlay_id=args.overlay_id, timeout_seconds=args.timeout_seconds,
            )
            print(overlaid.video_path)
            print(f"duration_seconds={overlaid.duration_seconds:.3f}")
            print(overlaid.manifest_path)
            return EXIT_OK

        if args.command == "transcript-request":
            artifact = load_transcript_artifact(args.input)
            written_path = write_document_request(
                artifact,
                args.output,
                issue_date=date.fromisoformat(args.issue_date),
                document_id=args.document_id,
                title=args.title,
                locale=args.locale,
            )
            print(written_path)
            return EXIT_OK
    except (ValidationError, DocumentRequestError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except ExtractionError as exc:
        print(f"media error: {exc}", file=sys.stderr)
        return EXIT_MEDIA_ERROR
    print(f"unknown command: {args.command}", file=sys.stderr)
    return EXIT_INPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
