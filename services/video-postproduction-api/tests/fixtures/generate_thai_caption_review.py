"""Generate local synthetic caption goldens and six owner-review clips."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from uuid import UUID

from video_postproduction.captions.ass import render_ass
from video_postproduction.edl import EDLDocument
from video_postproduction.render.ffmpeg import FFmpegRenderEngine
from video_postproduction.transcription.models import NormalizedTranscript, TranscriptSegment

CASES = [
    ("minimal", "วันนี้เราประชุมเพื่อวางแผนงานร่วมกัน", ["วันนี้เราประชุมเพื่อวางแผนงาน", "ร่วมกัน"]),
    ("bold", "สวัสดีครับ!วันนี้ประชุมเรื่องงาน?จบ", ["สวัสดีครับ!วันนี้ประชุมเรื่องงาน?", "จบ"]),
    ("emphasis", "วันนี้ประชุมเรื่องAPIและDockerครับ", ["วันนี้ประชุมเรื่องAPIและDocker", "ครับ"]),
]


def generate_review(output: Path) -> list[dict[str, object]]:
    output.mkdir(parents=True, exist_ok=True)
    font = subprocess.run(
        ["fc-match", "-f", "%{family}", "Noto Sans Thai"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert "Noto Sans Thai" in font.stdout, "Thai runtime font is missing"
    source = output / "synthetic-source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=1920x1080:r=10:d=3", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=16000:duration=3",
         "-c:v", "libx264", "-threads", "1", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True, timeout=60,
    )
    results = []
    for case, (preset, text, portrait_lines) in enumerate(CASES, 1):
        transcript = NormalizedTranscript(
            provider="synthetic-golden", model="manual-fixture", language="th", duration=3,
            segments=[TranscriptSegment(id="segment-1", start=0.25, end=2.5, text=text)],
        )
        for orientation, width, height in [("portrait", 1080, 1920), ("landscape", 1920, 1080)]:
            name = f"case-{case}-{orientation}"
            caption = output / f"{name}.ass"
            ass = render_ass(transcript, preset, output_size=(width, height))
            dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
            expected = portrait_lines if orientation == "portrait" else [text]
            assert dialogue.split(",", 9)[9].split("\\N") == expected, "Golden word boundary changed"
            caption.write_text(ass, encoding="utf-8")
            asset_id = UUID(int=2)
            edl = EDLDocument.model_validate({
                "version": 1, "project_id": str(UUID(int=1)),
                "source_assets": [{"asset_id": str(asset_id), "storage_key": source.name, "duration": 3}],
                "timeline": [{
                    "id": "golden-1", "source": {"asset_id": str(asset_id), "start": 0, "end": 3},
                    "visual": {"kind": "a_roll", "reframe": (
                        {"mode": "center", "aspect_ratio": "9:16"} if orientation == "portrait" else None
                    )},
                    "captions": {"enabled": True, "format": "ass", "preset": preset},
                    "audio": {"operations": []}, "color": {"operations": []},
                }],
                "assets": [], "output": {"width": width, "height": height, "fps": 10,
                                        "render_profile": "vertical_centered" if orientation == "portrait" else "source"},
                "capabilities": {"required": ["cut", "captions"]},
            })
            clip = output / f"{name}.mp4"
            artifact = FFmpegRenderEngine(timeout_seconds=120).render(
                edl, input_path=source, output_path=clip, caption_path=caption,
            )
            png = output / f"{name}.png"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "1", "-i", str(clip),
                 "-frames:v", "1", "-threads", "1", str(png)],
                check=True, capture_output=True, timeout=30,
            )
            pixels = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(png),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-threads", "1", "pipe:1"],
                check=True, capture_output=True, timeout=30,
            ).stdout
            assert len(pixels) == width * height, "Golden frame dimensions changed"
            occupied = [index for index, pixel in enumerate(pixels) if pixel > 100]
            assert occupied, "Caption frame contains no visible glyphs"
            left = min(index % width for index in occupied)
            right = max(index % width for index in occupied)
            bottom = max(index // width for index in occupied)
            assert left >= 40 and right < width - 40, "Caption reaches the frame edge"
            assert bottom < height - 10, "Caption is clipped at the bottom"
            results.append({
                "case": case, "orientation": orientation, "width": width, "height": height,
                "duration": artifact.duration, "golden_lines": len(expected),
                "glyph_bounds": [left, right, bottom],
                "frame_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                "clip_sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
                "synthetic": True, "audio": "440Hz tone, not speech",
            })
    (output / "review-manifest.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8",
    )
    return results


if __name__ == "__main__":
    import sys

    print(json.dumps(generate_review(Path(sys.argv[1]))))
